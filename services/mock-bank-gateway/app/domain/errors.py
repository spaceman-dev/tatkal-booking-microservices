class RuleViolation(Exception):
    """A business rule (BRn) was broken. Domain code raises it, the API layer turns it
    into a 422 problem response."""

    def __init__(self, rule: str, code: str, message: str):
        super().__init__(message)
        self.rule = rule
        self.code = code
        self.message = message
