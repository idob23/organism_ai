from dataclasses import dataclass


@dataclass
class ValidationResult:
    allowed: bool
    reason: str = ""
    requires_confirmation: bool = False


# Operations that are never allowed
BLACKLIST = [
    "os.system", "subprocess", "shutil.rmtree",
    "os.remove", "os.unlink", "os.rmdir",
    "open('/etc", "open('/proc", "open('/sys",
    "os.environ", "__import__('os').system",
    "eval(", "exec(compile(",
]

# Operations that require user confirmation
YELLOWLIST = [
    "requests.post", "requests.put", "requests.delete",
    "smtplib", "socket.connect",
]


class SafetyValidator:

    def validate_code(self, code: str) -> ValidationResult:
        """Check code before execution."""
        for pattern in BLACKLIST:
            if pattern in code:
                return ValidationResult(
                    allowed=False,
                    reason=f"Blocked pattern detected: '{pattern}'",
                )

        for pattern in YELLOWLIST:
            if pattern in code:
                return ValidationResult(
                    allowed=True,
                    reason=f"Sensitive operation detected: '{pattern}'",
                    requires_confirmation=True,
                )

        return ValidationResult(allowed=True)

    def validate_domains(self, domains: list[str]) -> ValidationResult:
        """Check requested network domains."""
        suspicious = [d for d in domains if any(
            s in d for s in ["localhost", "127.0.0.1", "169.254", "10.", "192.168."]
        )]
        if suspicious:
            return ValidationResult(
                allowed=False,
                reason=f"Internal network access not allowed: {suspicious}",
            )
        return ValidationResult(allowed=True)
