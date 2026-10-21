class RuleViolation(Exception):
    """A business rule (BRn) was broken. Raised by domain code, which knows
    nothing about HTTP; the API layer maps it to a 422 problem response."""

    def __init__(self, rule: str, code: str, message: str):
        super().__init__(message)
        self.rule = rule
        self.code = code
        self.message = message
