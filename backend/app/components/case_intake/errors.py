class CaseIntakeError(RuntimeError):
    code = "case_intake_error"


class CaseNotFound(CaseIntakeError):
    code = "case_not_found"


class CaseRequestConflict(CaseIntakeError):
    code = "request_conflict"


class CasePersistenceError(CaseIntakeError):
    code = "case_persistence_error"
