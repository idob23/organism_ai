import asyncio
import ast
import platform
import shutil
import tempfile
import threading
import uuid
import os
from pathlib import Path
from typing import Any

import docker
import docker.errors

from config.settings import settings
from src.organism.logging.error_handler import get_logger
from .base import BaseTool, ToolResult

_log = get_logger("tools.code_executor")

IS_WINDOWS = platform.system() == "Windows"


def _docker_host_path(path: str) -> str:
    """Convert a host path to Docker-compatible mount path on Windows.

    Docker Desktop on Windows requires /c/Users/... format instead of C:\\Users\\...
    On Linux/macOS the path is returned unchanged.
    """
    if not IS_WINDOWS:
        return path
    p = Path(path).as_posix()          # C:/Users/ID/AppData/...
    if len(p) >= 2 and p[1] == ":":
        p = "/" + p[0].lower() + p[2:]  # /c/Users/ID/AppData/...
    return p


class CodeExecutorTool(BaseTool):

    SANDBOX_IMAGE = "organism-sandbox"

    def __init__(self) -> None:
        self._client = docker.from_env()
        # FIX-50: Warm container pool
        self._warm = None
        self._warm_sandbox = None
        self._warm_output = None
        self._warm_lock = threading.Lock()
        self._init_warm()

    def _init_warm(self) -> None:
        """Start a warm container with sleep infinity for reuse."""
        try:
            self._warm_sandbox = tempfile.mkdtemp(prefix="organism_warm_sb_")
            self._warm_output = tempfile.mkdtemp(prefix="organism_warm_out_")
            outputs_dir = os.path.join(os.getcwd(), "data", "outputs")
            os.makedirs(outputs_dir, exist_ok=True)
            self._warm = self._client.containers.run(
                image=self.SANDBOX_IMAGE,
                command=["sleep", "infinity"],
                name=f"organism-warm-{uuid.uuid4().hex[:6]}",
                network_mode="none",
                mem_limit=settings.sandbox_memory,
                nano_cpus=int(settings.sandbox_cpu * 1e9),
                volumes={
                    _docker_host_path(self._warm_sandbox): {"bind": "/sandbox", "mode": "ro"},
                    _docker_host_path(self._warm_output): {"bind": "/output", "mode": "rw"},
                    _docker_host_path(outputs_dir): {"bind": "/data/outputs", "mode": "ro"},
                },
                working_dir="/output",
                detach=True,
                remove=False,
            )
            _log.info("Warm container started: %s", self._warm.name)
        except Exception as e:
            _log.debug("Warm container init failed (will use cold): %s", e)
            self._warm = None

    def __del__(self) -> None:
        try:
            if self._warm:
                self._warm.remove(force=True)
        except Exception:
            pass
        for d in (self._warm_sandbox, self._warm_output):
            try:
                if d:
                    shutil.rmtree(d, ignore_errors=True)
            except Exception:
                pass

    @property
    def name(self) -> str:
        return "code_executor"

    @property
    def description(self) -> str:
        return (
            "Execute Python code in an isolated Docker sandbox. "
            "Use for calculations, data processing, file operations, scripting. "
            "Output via print(). NO internet access inside sandbox.\n"
            "PATHS: Read existing files from /data/outputs/ (read-only). "
            "ALWAYS save new or updated files to /output/ (writable). "
            "Print 'Saved files: filename.ext' after saving."
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
        # FIX-50: Try warm container first, fall back to cold
        if self._warm:
            try:
                return self._run_warm(code)
            except Exception as e:
                _log.warning("Warm exec failed (falling back to cold): %s", e)
        return self._run_cold(code, domains)

    def _run_warm(self, code: str) -> ToolResult:
        """Execute code in the warm container via exec_run."""
        with self._warm_lock:
            # Check container is alive
            self._warm.reload()
            if self._warm.status != "running":
                self._warm = None
                raise RuntimeError("Warm container not running")

            # Clean output dir from previous run
            for f in os.listdir(self._warm_output):
                p = os.path.join(self._warm_output, f)
                try:
                    os.unlink(p) if os.path.isfile(p) else shutil.rmtree(p)
                except Exception:
                    pass

            # Write code to sandbox dir
            code_path = os.path.join(self._warm_sandbox, "code.py")
            with open(code_path, "w", encoding="utf-8") as f:
                f.write(code)

            # Execute with timeout
            timeout_s = settings.sandbox_timeout
            exit_code, output = self._warm.exec_run(
                ["timeout", str(timeout_s), "python", "/sandbox/code.py"],
                workdir="/output",
                demux=True,
            )
            stdout = (output[0] or b"").decode("utf-8", errors="replace").strip()
            stderr = (output[1] or b"").decode("utf-8", errors="replace").strip()
            # FIX-52: Log warm exec result for debugging
            _log.warning("Warm exec: exit=%s stdout=%r stderr=%r",
                exit_code, stdout[:150], stderr[:150])

            # exit code 124 = timeout killed the process
            if exit_code == 124:
                return ToolResult(output="", error=f"Timeout ({timeout_s}s)", exit_code=-1)

            # Copy output files to data/outputs
            saved_files = []
            outputs_host = os.path.join(os.getcwd(), "data", "outputs")
            os.makedirs(outputs_host, exist_ok=True)
            for fname in os.listdir(self._warm_output):
                src = os.path.join(self._warm_output, fname)
                if os.path.isfile(src):
                    dst = os.path.join(outputs_host, fname)
                    shutil.copy2(src, dst)
                    saved_files.append(fname)

            if saved_files:
                file_note = f"\nSaved files: {', '.join(saved_files)}"
                stdout = stdout + file_note if stdout else file_note

            return ToolResult(output=stdout, error=stderr, exit_code=exit_code,
                              created_files=saved_files)

    def _run_cold(self, code: str, domains: list[str]) -> ToolResult:
        """Original: create a new container per execution."""
        container_name = f"organism-sandbox-{uuid.uuid4().hex[:8]}"
        tmp_dir = tempfile.mkdtemp(prefix="organism_")
        code_path = os.path.join(tmp_dir, "code.py")
        output_dir = os.path.join(tmp_dir, "output")
        os.makedirs(output_dir, exist_ok=True)

        try:
            with open(code_path, "w", encoding="utf-8") as f:
                f.write(code)

            # FIX-38: mount data/outputs so sandbox can read previously created files
            outputs_dir = os.path.join(os.getcwd(), "data", "outputs")
            os.makedirs(outputs_dir, exist_ok=True)

            container = self._client.containers.run(
                image=self.SANDBOX_IMAGE,
                command=["python", "/sandbox/code.py"],
                name=container_name,
                network_mode="none",
                mem_limit=settings.sandbox_memory,
                nano_cpus=int(settings.sandbox_cpu * 1e9),
                volumes={
                    _docker_host_path(tmp_dir): {"bind": "/sandbox", "mode": "ro"},
                    _docker_host_path(output_dir): {"bind": "/output", "mode": "rw"},
                    _docker_host_path(outputs_dir): {"bind": "/data/outputs", "mode": "ro"},
                },
                working_dir="/output",
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

            # Copy any output files to the host working directory
            saved_files = []
            outputs_dir = os.path.join(os.getcwd(), "data", "outputs")
            os.makedirs(outputs_dir, exist_ok=True)
            for fname in os.listdir(output_dir):
                src = os.path.join(output_dir, fname)
                if os.path.isfile(src):
                    dst = os.path.join(outputs_dir, fname)
                    shutil.copy2(src, dst)
                    saved_files.append(fname)

            if saved_files:
                file_note = f"\nSaved files: {', '.join(saved_files)}"
                stdout = stdout + file_note if stdout else file_note

            return ToolResult(output=stdout, error=stderr, exit_code=exit_code,
                              created_files=saved_files)

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
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception:
                pass
