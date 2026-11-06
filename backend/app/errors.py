class RainScopeAPIError(Exception):
    def __init__(self, status_code: int, code: str, message: str, context: dict[str, object] | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.context = context or {}


class ForecastNotPublishedError(Exception):
    """ECMWF's index confirms that the requested run/step is not published."""


class ForecastNetworkError(Exception):
    """A network request to the configured ECMWF Open Data source failed."""


class ForecastDecodeError(Exception):
    """A cached or downloaded GRIB could not be validated or decoded."""
