from __future__ import annotations


class AIReviewError(RuntimeError):
    """Base error for the AI review backend."""


class InvalidMatchDataError(AIReviewError):
    pass


class SelfPlayerNotFoundError(AIReviewError):
    pass


class TargetSelectionError(AIReviewError):
    pass


class MissingAPIKeyError(AIReviewError):
    pass


class DeepSeekAPIError(AIReviewError):
    def __init__(self, message: str, *, status_code: int | None = None, body: str = "") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class DeepSeekAuthenticationError(DeepSeekAPIError):
    pass


class DeepSeekRateLimitError(DeepSeekAPIError):
    pass


class DeepSeekNetworkError(DeepSeekAPIError):
    pass


class ReviewBusyError(AIReviewError):
    pass


class ReviewCancelled(AIReviewError):
    pass
