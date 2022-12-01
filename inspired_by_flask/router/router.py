try:
    from .http_utils import HttpMethod
except ImportError:
    # for testing
    from http_utils import HttpMethod

from typing import Any, Generic, TypeVar, Callable, Iterator
from re import compile
from collections import defaultdict
from abc import ABC, abstractmethod
from urllib.parse import urljoin

_URL_PARAMS_FINDER = compile(r"(\<.+?\>)")
_URL_PARAMS_TYPE_FINDER = compile(r"\<((?P<type>.+):)?(?P<name>.+){1}\>")
_PARAM_TYPE_MAPPER = defaultdict(lambda: str, {"int": int, "float": float})
"""
>>> oll = _URL_PARAMS_FINDER.findall("/url/format/<int:name1>/<name2>")
>>> oll
['<int:name1>', '<name2>']
>>> param = _URL_PARAMS_TYPE_FINDER.match(oll[0])
>>> param.group("type")
'int'
"""

T = TypeVar("T")


class UrlFormatError(ValueError):
    pass


class UrlParamFormatter(Generic[T]):
    converter: T = None

    def __init__(self, converter: T) -> None:
        self.converter = converter

    def is_convertable(self, value: str) -> bool:
        try:
            self.convert(value)
            return True
        except ValueError:
            return False

    def convert(self, value: str) -> T:
        return self.converter(value)


def parse_url(url_format: str, url: str) -> dict[str, str]:
    """
    from format /url/format/<int:name1>/<name2>
    url="/url/format/1/oh_yeah" -> return {"name1": "1", "name2": "oh_yeah"}
    url="/url/format/1" -> raise UrlFormatError("url missformatted")
    """
    params_dict = {}
    url_format_list = url_format.split("/")
    url_list = url.split("/")
    if len(url_format_list) != len(url_list):
        raise UrlFormatError(f"len of format url is different from given url")

    for format_part, request_part in zip(url_format_list, url_list):
        if format_part == request_part:
            continue
        param = _URL_PARAMS_TYPE_FINDER.match(format_part)
        if not param:
            raise UrlFormatError(
                "part of the path of the url was different and wasn't a param"
            )
        params_dict[param.group("name")] = request_part
    return params_dict


def from_url_get_required_params(url_format: str) -> dict[str, UrlParamFormatter]:
    """
    from format /url/format/<int:name1>/<name2>
    url="/url/format/1/oh_yeah" -> return {"name1": UrlParamFormatter[int], "name2": UrlParamFormatter[str]}
    """
    params_formatters = {}
    for param in _URL_PARAMS_FINDER.findall(url_format):
        param = _URL_PARAMS_TYPE_FINDER.match(param)
        param_type = param.group("type") if param is not None else "str"
        param_type = _PARAM_TYPE_MAPPER[param_type]
        params_formatters[param.group("name")] = UrlParamFormatter[param_type](
            param_type
        )
    return params_formatters


def from_url_get_required_params_names(url_format: str) -> Iterator[str]:
    """
    from format /url/format/<int:name1>/<name2>
    return Iterator("name1","name2")
    """
    for param in _URL_PARAMS_FINDER.findall(url_format):
        param = _URL_PARAMS_TYPE_FINDER.match(param)
        yield param.group("name")


class Route(ABC):
    mapped_url: str = ""
    accepted_methods: list[HttpMethod] = []

    @abstractmethod
    def __init__(self) -> None:
        raise NotImplementedError()

    def __eq__(self, __o: object) -> bool:
        if issubclass(__o.__class__, Route):
            return (
                self.mapped_url == __o.mapped_url
                and self.accepted_methods == __o.accepted_methods
            )
        else:
            raise ValueError(f"== not supported for type {type(__o)}")

    def validate_method(self, method: HttpMethod):
        return method in self.accepted_methods

    @abstractmethod
    def validate_url(self, url: str) -> bool:
        raise NotImplementedError()

    @abstractmethod
    def parse_url(self, url: str) -> tuple[Callable, dict]:
        raise NotImplementedError()


class SimpleRoute(Route):
    handler: Callable = print
    __reqired_url_params: dict[str, UrlParamFormatter] = {}

    def __init__(
        self,
        url: str,
        handler: Callable,
        accepted_methods: list[HttpMethod],
    ) -> None:
        self.mapped_url = url
        self.accepted_methods = accepted_methods
        self.__reqired_url_params = from_url_get_required_params(url)
        self.handler = handler

    def validate_url(self, url: str) -> bool:
        try:
            return all(
                self.__reqired_url_params[key].is_convertable(value)
                for key, value in parse_url(self.mapped_url, url).items()
            )
        except UrlFormatError:
            return False

    def parse_url(self, url: "str") -> tuple[Callable, dict]:
        return (
            self.handler,
            {
                key: self.__reqired_url_params[key].convert(value)
                for key, value in parse_url(self.mapped_url, url).items()
            },
        )


class DefaultRoute(SimpleRoute):
    def __init__(
        self,
        url: str,
        handler: Callable,
    ) -> None:
        super().__init__(url, handler, [])

    def parse_url(self, url: "str") -> tuple[Callable, None]:
        return self.handler, None


class NestedRoute(Route):
    mapped_route: Route
    __default_url_params_str: str = {}

    def __init__(
        self,
        url: str,
        mapped_route: SimpleRoute,
        accepted_methods: list[HttpMethod],
        default_url_params: dict[str, Any],
    ) -> None:
        self.mapped_url = url
        self.accepted_methods = accepted_methods
        # extract url params names in order so we can append in the url
        # the default values in the correct order
        self.mapped_route = mapped_route
        self.__default_url_params_str = "/".join(
            (
                str(default_url_params[param_name])
                for param_name in from_url_get_required_params_names(
                    self.mapped_route.mapped_url
                )
                if param_name in default_url_params
            )
        )

    def validate_url(self, url: str) -> bool:
        return self.mapped_route.validate_url(
            urljoin(url + "/", self.__default_url_params_str)
        )

    def parse_url(self, url: "str") -> tuple[Callable, dict]:
        return self.mapped_route.parse_url(
            urljoin(url + "/", self.__default_url_params_str)
        )


class RouteSet:
    __all_routes: list[Route] = []
    __default_route: DefaultRoute = None

    def __init__(self, default_route: DefaultRoute) -> None:
        self.__default_route = default_route

    def set_default_route_handler(self, default_handler: Callable):
        self.__default_route.handler = default_handler

    def add_route(self, new_route: Route) -> bool:
        for route in self.__all_routes:
            if route == new_route:
                return False

        self.__all_routes.append(new_route)
        return True

    def get_route(self, __url: str, method: HttpMethod) -> Route:
        return next(
            filter(
                lambda x: x.validate_method(method) and x.validate_url(__url),
                self.__all_routes,
            ),
            self.__default_route,
        )


def _default_handler_not_set(*args, **kwargs):
    raise NotImplemented("Default handler not set")


class Router:
    routes: RouteSet = None

    def __init__(self, default_handler: Callable = _default_handler_not_set) -> None:
        self.routes = RouteSet(DefaultRoute("", default_handler))

    def set_default_handler(self, default_handler: Callable):
        self.routes.set_default_route_handler(default_handler)

    def get_handler(
        self, __url: str, method: HttpMethod
    ) -> tuple[Callable, dict | None]:
        """
        get handler for specified __url and method
        if return is Callable,None, the default_handler is returned
        """
        return self.routes.get_route(__url, method).parse_url(__url)

    def add_route(
        self,
        url: str,
        handler: Callable | Route,
        accepted_methods: list[HttpMethod] = [HttpMethod.GET],
        default_params: dict[str, Any] = {},
    ) -> Route:

        new_route = None
        if isinstance(handler, SimpleRoute):
            if not default_params:
                raise ValueError("for NestedRoute default_params are required")
            new_route = NestedRoute(url, handler, accepted_methods, default_params)
        elif isinstance(handler, Callable):
            new_route = SimpleRoute(url, handler, accepted_methods)
        else:
            raise ValueError(
                f"routing not implemented for handler of type {type(handler)}"
            )

        self.routes.add_route(new_route)
        return new_route

    def route(
        self,
        url: str,
        accepted_methods: list[HttpMethod] = [HttpMethod.GET],
        default_params: dict[str, Any] = {},
    ) -> Route:
        """decorator, same functionality of add_route"""

        def decorate(handler):
            return self.add_route(url, handler, accepted_methods, default_params)

        return decorate


if __name__ == "__main__":
    # testing code
    router = Router(lambda: print("DEFAULT HANDLER"))
    router.add_route(
        "/test/this/url", lambda x: print(f"GET /test/this/url {x}"), [HttpMethod.GET]
    )
    router.add_route(
        "/test/this/url", lambda x: print(f"POST /test/this/url {x}"), [HttpMethod.POST]
    )

    @router.route(
        "/test/<int:this>",
        [HttpMethod.GET, HttpMethod.POST],
        {"url": "bar"},
    )
    @router.route("/test/<int:this>/<url>", [HttpMethod.GET, HttpMethod.POST])
    def oll(this=None, url=None):
        print(f"handler POST/GET parameters {this=}  {url=}")

    handler, params = router.get_handler("/test/this/url", HttpMethod.GET)
    handler(params)
    handler, params = router.get_handler("/test/this/url", HttpMethod.POST)
    handler(params)
    handler, params = router.get_handler("/test/1/foo", HttpMethod.POST)
    handler(**params)
    handler, params = router.get_handler("/test/1", HttpMethod.POST)
    handler(**params)
    pass
