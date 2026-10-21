# a broken business rule (BRn), raised by domain code. The API turns it into a 422.
class RuleViolation(Exception):
    def __init__(self, rule: str, code: str, message: str):
        super().__init__(message)
        self.rule = rule
        self.code = code
        self.message = message
