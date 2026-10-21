class RuleViolation(Exception):
    """A business rule (BRn) was broken. Domain code knows nothing about HTTP, the
    error handler turns this into a 422."""

    def __init__(self, rule: str, code: str, message: str):
        super().__init__(message)
        self.rule = rule
        self.code = code
        self.message = message
