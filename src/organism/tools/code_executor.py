import asyncio
import base64
import uuid
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
            "Use for calculations, data processing, file operations, and scripting. "
            "Output via print(). For multi-step data passing, use /tmp/ files."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Python code to execute",
                },
                "domains": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Allowed domains for network access. Empty = no network.",
                    "default": [],
                },
            },
            "required": ["code"],
        }

    async def execute(self, input: dict[str, Any]) -> ToolResult:
        code: str = input["code"]
        domains: list[str] = input.get("domains", [])
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._run_container, code, domains)

    def _run_container(self, code: str, domains: list[str]) -> ToolResult:
        container_name = f"organism-sandbox-{uuid.uuid4().hex[:8]}"

        # Encode code as base64 to safely pass via environment variable
        code_b64 = base64.b64encode(code.encode("utf-8")).decode("ascii")

        # Decode and run: python -c "import base64; exec(base64.b64decode(...))"
        runner = (
            f"import base64, sys; "
            f"exec(base64.b64decode('{code_b64}').decode('utf-8'))"
        )

        try:
            container = self._client.containers.run(
                image=self.SANDBOX_IMAGE,
                command=["python", "-c", runner],
                name=container_name,
                network_mode="none",
                mem_limit=settings.sandbox_memory,
                nano_cpus=int(settings.sandbox_cpu * 1e9),
                detach=True,
                remove=False,
            )

            try:
                exit_info = container.wait(timeout=settings.sandbox_timeout)
                exit_code = exit_info.get("StatusCode", 0)
            except Exception:
                container.kill()
                return ToolResult(
                    output="",
                    error=f"Execution timeout ({settings.sandbox_timeout}s exceeded)",
                    exit_code=-1,
                )

            stdout = container.logs(stdout=True, stderr=False).decode("utf-8", errors="replace").strip()
            stderr = container.logs(stdout=False, stderr=True).decode("utf-8", errors="replace").strip()

            return ToolResult(
                output=stdout,
                error=stderr,
                exit_code=exit_code,
            )

        except docker.errors.ImageNotFound:
            return ToolResult(
                output="",
                error=f"Sandbox image '{self.SANDBOX_IMAGE}' not found. Run: docker build -t organism-sandbox ./sandbox",
                exit_code=-1,
            )
        except Exception as e:
            return ToolResult(output="", error=str(e), exit_code=-1)
        finally:
            try:
                self._client.containers.get(container_name).remove(force=True)
            except Exception:
                pass
