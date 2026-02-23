import asyncio
import ast
import tempfile
import uuid
import os
from typing import Any

import docker
import docker.errors

from config.settings import settings
from .base import BaseTool, ToolResult


class CodeExecutorTool(BaseTool):

    SANDBOX_IMAGE = "organism-sandbox"

    def __init__(self) -> None:
        self._client = docker.from_env()

    @property
    def name(self) -> str:
        return "code_executor"

    @property
    def description(self) -> str:
        return (
            "Execute Python code in an isolated Docker sandbox. "
            "Use for calculations, data processing, file operations, scripting. "
            "Output via print(). NO internet access inside sandbox."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python code to execute"},
                "domains": {"type": "array", "items": {"type": "string"}, "default": []},
                "task_description": {"type": "string", "default": ""},
                "context": {"type": "string", "default": ""},
            },
            "required": ["code"],
        }

    def _is_stub(self, code: str) -> bool:
        code_stripped = code.strip()
        return len(code_stripped) < 120 and code_stripped.startswith("#")

    def _is_valid_python(self, code: str) -> bool:
        try:
            ast.parse(code)
            return True
        except SyntaxError:
            return False

    def _has_print(self, code: str) -> bool:
        return "print(" in code

    async def execute(self, input: dict[str, Any]) -> ToolResult:
        code: str = input["code"]
        domains: list[str] = input.get("domains", [])
        task_description: str = input.get("task_description", "")
        context: str = input.get("context", "")

        if self._is_stub(code):
            description = task_description or code.lstrip("#").strip()
            code = await self._generate_code(description, context)

        # Validate syntax
        if not self._is_valid_python(code):
            description = task_description or "Fix and complete: " + code[:200]
            code = await self._generate_code(description, context)
            if not self._is_valid_python(code):
                return ToolResult(output="", error="Generated code has syntax errors", exit_code=1)

        # Ensure code has print statements
        if not self._has_print(code):
            code = code + '\nprint("Done.")'

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, self._run_container, code, domains)

        # If output empty but no error  regenerate with explicit print requirement
        if not result.output and result.exit_code == 0:
            description = task_description or "Execute and print results: " + code[:200]
            code = await self._generate_code(description + "\n\nIMPORTANT: Use print() for ALL output.", context)
            if self._is_valid_python(code):
                result = await loop.run_in_executor(None, self._run_container, code, domains)

        return result

    async def _generate_code(self, description: str, context: str) -> str:
        from src.organism.llm.claude import ClaudeProvider
        from src.organism.llm.base import Message

        llm = ClaudeProvider()
        prompt = f"Write Python code to: {description}"
        if context:
            prompt += f"\n\nAvailable data:\n{context[:1500]}"
        prompt += (
            "\n\nRules:"
            "\n- MUST use print() for ALL output  no output = failure"
            "\n- Use only standard library + numpy + pandas"
            "\n- Keep code simple, under 80 lines"
            "\n- No complex f-strings  use variables for complex expressions"
            "\n- All strings must be properly terminated"
        )

        response = await llm.complete(
            messages=[Message(role="user", content=prompt)],
            system=(
                "Write ONLY executable Python code. "
                "No markdown, no backticks, no explanation. "
                "Raw Python only. "
                "CRITICAL: Every computation result MUST be printed with print(). "
                "Keep it simple and short."
            ),
            model_tier="balanced",
            max_tokens=3000,
        )

        code = response.content.strip()
        if "```python" in code:
            code = code.split("```python")[1].split("```")[0].strip()
        elif "```" in code:
            code = code.split("```")[1].split("```")[0].strip()

        return code

    def _run_container(self, code: str, domains: list[str]) -> ToolResult:
        container_name = f"organism-sandbox-{uuid.uuid4().hex[:8]}"
        tmp_dir = tempfile.mkdtemp(prefix="organism_")
        code_path = os.path.join(tmp_dir, "code.py")

        try:
            with open(code_path, "w", encoding="utf-8") as f:
                f.write(code)

            container = self._client.containers.run(
                image=self.SANDBOX_IMAGE,
                command=["python", "/sandbox/code.py"],
                name=container_name,
                network_mode="none",
                mem_limit=settings.sandbox_memory,
                nano_cpus=int(settings.sandbox_cpu * 1e9),
                volumes={tmp_dir: {"bind": "/sandbox", "mode": "ro"}},
                detach=True,
                remove=False,
            )

            try:
                exit_info = container.wait(timeout=settings.sandbox_timeout)
                exit_code = exit_info.get("StatusCode", 0)
            except Exception:
                container.kill()
                return ToolResult(output="", error=f"Timeout ({settings.sandbox_timeout}s)", exit_code=-1)

            stdout = container.logs(stdout=True, stderr=False).decode("utf-8", errors="replace").strip()
            stderr = container.logs(stdout=False, stderr=True).decode("utf-8", errors="replace").strip()

            return ToolResult(output=stdout, error=stderr, exit_code=exit_code)

        except docker.errors.ImageNotFound:
            return ToolResult(output="", error="Sandbox image not found. Run: docker build -t organism-sandbox ./sandbox", exit_code=-1)
        except Exception as e:
            return ToolResult(output="", error=str(e), exit_code=-1)
        finally:
            try:
                self._client.containers.get(container_name).remove(force=True)
            except Exception:
                pass
            try:
                import shutil
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception:
                pass
