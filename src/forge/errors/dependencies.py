class DependencyUnavailableError(Exception):
    def __init__(self, dependency: str, *, retry_after_seconds: int = 1) -> None:
        self.dependency = dependency
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"{dependency} is temporarily unavailable")
