"""SEC-1: Sandbox isolation regression tests.

Verify that Docker sandbox never mounts secret directories.
Tests require Docker and the organism-sandbox image.
Skip gracefully if unavailable.
"""
import platform
import uuid

import pytest

IS_WINDOWS = platform.system() == "Windows"


def _docker_available() -> bool:
    try:
        import docker
        client = docker.from_env()
        client.ping()
        return True
    except Exception:
        return False


def _sandbox_image_exists() -> bool:
    try:
        import docker
        client = docker.from_env()
        client.images.get("organism-sandbox")
        return True
    except Exception:
        return False


skip_no_docker = pytest.mark.skipif(
    not _docker_available(), reason="Docker not available",
)
skip_no_image = pytest.mark.skipif(
    not _sandbox_image_exists(), reason="organism-sandbox image not found",
)


def _docker_host_path(path: str) -> str:
    """Mirror of code_executor._docker_host_path for test use."""
    if not IS_WINDOWS:
        return path
    from pathlib import Path as P
    p = P(path).as_posix()
    if len(p) >= 2 and p[1] == ":":
        p = "/" + p[0].lower() + p[2:]
    return p


def _get_repo_volumes() -> dict:
    from src.organism.tools.code_executor import _repo_volumes
    return _repo_volumes()


@pytest.fixture
def docker_client():
    import docker
    return docker.from_env()


def _run_sandbox_code(docker_client, code: str) -> str:
    """Run Python code in sandbox container and return stdout."""
    vols = _get_repo_volumes()
    name = f"sec1-test-{uuid.uuid4().hex[:6]}"
    try:
        container = docker_client.containers.run(
            image="organism-sandbox",
            command=["python", "-c", code],
            name=name,
            network_mode="none",
            mem_limit="128m",
            volumes=vols,
            detach=True,
            remove=False,
        )
        container.wait(timeout=10)
        return container.logs().decode("utf-8", errors="replace").strip()
    finally:
        try:
            docker_client.containers.get(name).remove(force=True)
        except Exception:
            pass


# -- Tests --------------------------------------------------------------------


@skip_no_docker
@skip_no_image
@pytest.mark.parametrize("container_type", ["warm", "cold"])
def test_gmail_secrets_not_mounted(docker_client, container_type):
    """config/gmail/ must not be accessible inside sandbox."""
    output = _run_sandbox_code(
        docker_client,
        "import os; print(os.path.exists('/repo/config/gmail'))",
    )
    assert output == "False", f"config/gmail/ accessible in sandbox ({container_type})"


@skip_no_docker
@skip_no_image
@pytest.mark.parametrize("container_type", ["warm", "cold"])
def test_data_secrets_not_mounted(docker_client, container_type):
    """data/secrets/ must not be accessible inside sandbox."""
    output = _run_sandbox_code(
        docker_client,
        "import os; print(os.path.exists('/data/secrets'))",
    )
    assert output == "False", f"data/secrets/ accessible in sandbox ({container_type})"


@skip_no_docker
@skip_no_image
@pytest.mark.parametrize("container_type", ["warm", "cold"])
def test_gmail_token_read_fails(docker_client, container_type):
    """Reading gmail token must fail, not leak content."""
    code = (
        "try:\n"
        "  open('/repo/config/gmail/token.json').read()\n"
        "  print('LEAK')\n"
        "except Exception as e:\n"
        "  print(type(e).__name__)"
    )
    output = _run_sandbox_code(docker_client, code)
    assert "LEAK" not in output, f"Token leaked in sandbox ({container_type})"
    assert "Error" in output or "error" in output.lower()


@skip_no_docker
@skip_no_image
@pytest.mark.parametrize("container_type", ["warm", "cold"])
def test_allowlisted_dirs_mounted(docker_client, container_type):
    """Safe config subdirectories must be accessible."""
    code = (
        "import os\n"
        "for d in ['prompts', 'roles', 'skills', 'personality', 'fonts']:\n"
        "    print(f'{d}={os.path.isdir(f\"/repo/config/{d}\")}')"
    )
    output = _run_sandbox_code(docker_client, code)
    for line in output.strip().split("\n"):
        if "=" in line:
            name, val = line.split("=", 1)
            assert val == "True", (
                f"config/{name}/ should be mounted ({container_type})"
            )
