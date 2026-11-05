"""Custom errors. Each one carries a clear, user-facing message so the CLI
can print it and exit cleanly instead of showing a Python traceback."""


class AnalyzerError(Exception):
    """Base class for every expected failure the tool reports to the user."""

    exit_code = 1


class InvalidURLError(AnalyzerError):
    """The input is not a GitHub repository URL we can understand."""

    exit_code = 2


class RepoNotFoundError(AnalyzerError):
    """GitHub says the repository does not exist (or is private)."""

    exit_code = 3


class RateLimitError(AnalyzerError):
    """The GitHub API rate limit is used up."""

    exit_code = 4


class NetworkError(AnalyzerError):
    """No connection, timeout, or an unexpected API response."""

    exit_code = 5
