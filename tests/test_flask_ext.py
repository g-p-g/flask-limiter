"""

"""
import json
import time

import logging
import unittest
from flask import Flask, Blueprint, request, current_app, make_response
import hiro
import mock
from flask.ext.limiter.errors import ConfigurationError
from flask.ext.limiter.extension import Limiter
from flask.ext.limiter.storage import MemcachedStorage
from flask.ext.limiter.strategies import MovingWindowRateLimiter


class FlaskExtTests(unittest.TestCase):

    def build_app(self, config={}, **limiter_args):
        app = Flask(__name__)
        for k,v in config.items():
            app.config.setdefault(k,v)
        limiter = Limiter(app, **limiter_args)
        mock_handler = mock.Mock()
        mock_handler.level = logging.INFO
        limiter.logger.addHandler(mock_handler)
        return app, limiter



    def test_invalid_strategy(self):
        app = Flask(__name__)
        app.config.setdefault("RATELIMIT_STRATEGY", "fubar")
        self.assertRaises(ConfigurationError, Limiter, app)

    def test_invalid_storage_string(self):
        app = Flask(__name__)
        app.config.setdefault("RATELIMIT_STORAGE_URL", "fubar://localhost:1234")
        self.assertRaises(ConfigurationError, Limiter, app)

    def test_constructor_arguments_over_config(self):
        app = Flask(__name__)
        app.config.setdefault("RATELIMIT_STRATEGY", "fixed-window-elastic-expiry")
        limiter = Limiter(strategy='moving-window')
        limiter.init_app(app)
        app.config.setdefault("RATELIMIT_STORAGE_URL", "redis://localhost:6379")
        self.assertEqual(type(limiter.limiter), MovingWindowRateLimiter)
        limiter = Limiter(storage_uri='memcached://localhost:11211')
        limiter.init_app(app)
        self.assertEqual(type(limiter.storage), MemcachedStorage)

    def test_error_message(self):
        app, limiter = self.build_app({
            "RATELIMIT_GLOBAL" : "1 per day"
        })
        @app.route("/")
        def null():
            return ""

        with app.test_client() as cli:
            cli.get("/")
            self.assertTrue("1 per 1 day" in cli.get("/").data.decode())
            @app.errorhandler(429)
            def ratelimit_handler(e):
                return make_response('{"error" : "rate limit %s"}' % str(e.description), 429)
            self.assertEqual({'error': 'rate limit 1 per 1 day'}, json.loads(cli.get("/").data.decode()))

    def test_combined_rate_limits(self):
        app, limiter = self.build_app({
            "RATELIMIT_GLOBAL" : "1 per hour; 10 per day"
        })

        @app.route("/t1")
        @limiter.limit("100 per hour;10/minute")
        def t1():
            return "t1"

        @app.route("/t2")
        def t2():
            return "t2"

        with hiro.Timeline().freeze() as timeline:
            with app.test_client() as cli:
                self.assertEqual(404, cli.get("/").status_code)
                self.assertEqual(429, cli.get("/").status_code)
                timeline.forward(60 * 60 + 1)
                self.assertEqual(404, cli.get("/").status_code)
                for i in range(0,100):
                    self.assertEqual(200, cli.get("/t1").status_code)
                    if not i % 10 == 0:
                        timeline.forward(60)
                self.assertEqual(200, cli.get("/t1").status_code)
                self.assertEqual(200, cli.get("/t2").status_code)
                self.assertEqual(429, cli.get("/t2").status_code)

    def test_key_func(self):
        app, limiter = self.build_app()
        @app.route("/t1")
        @limiter.limit("100 per minute", lambda:"test")
        def t1():
            return "test"

        with hiro.Timeline().freeze() as timeline:
            with app.test_client() as cli:
                for i in range(0,100):
                    self.assertEqual(200,
                                     cli.get("/t1", headers = {"X_FORWARDED_FOR":"127.0.0.2"}).status_code
                    )
                self.assertEqual(429, cli.get("/t1").status_code)

    def test_multiple_decorators(self):
        app, limiter = self.build_app()
        @app.route("/t1")
        @limiter.limit("100 per minute", lambda:"test") # effectively becomes a limit for all users
        @limiter.limit("50/minute") # per ip as per default key_func
        def t1():
            return "test"

        with hiro.Timeline().freeze() as timeline:
            with app.test_client() as cli:
                for i in range(0,100):
                    self.assertEqual(200 if i < 50 else 429,
                                     cli.get("/t1", headers = {"X_FORWARDED_FOR":"127.0.0.2"}).status_code
                    )
                self.assertEqual(429, cli.get("/t1").status_code)

    def test_logging(self):
        app = Flask(__name__)
        limiter = Limiter(app)
        mock_handler = mock.Mock()
        mock_handler.level = logging.INFO
        limiter.logger.addHandler(mock_handler)
        @app.route("/t1")
        @limiter.limit("1/minute")
        def t1():
            return "test"
        with app.test_client() as cli:
            self.assertEqual(200,cli.get("/t1").status_code)
            self.assertEqual(429,cli.get("/t1").status_code)
        self.assertEqual(mock_handler.handle.call_count, 1)

    def test_reuse_logging(self):
        app = Flask(__name__)
        app_handler = mock.Mock()
        app_handler.level = logging.INFO
        app.logger.addHandler(app_handler)
        limiter = Limiter(app)
        for handler in app.logger.handlers:
            limiter.logger.addHandler(handler)
        @app.route("/t1")
        @limiter.limit("1/minute")
        def t1():
            return "42"

        with app.test_client() as cli:
            cli.get("/t1")
            cli.get("/t1")

        self.assertEqual(app_handler.handle.call_count, 1)

    def test_exempt_routes(self):
        app, limiter = self.build_app(global_limits = ["1/minute"])

        @app.route("/t1")
        def t1():
            return "test"

        @app.route("/t2")
        @limiter.exempt
        def t2():
            return "test"

        with app.test_client() as cli:
            self.assertEqual(cli.get("/t1").status_code, 200)
            self.assertEqual(cli.get("/t1").status_code, 429)
            self.assertEqual(cli.get("/t2").status_code, 200)
            self.assertEqual(cli.get("/t2").status_code, 200)


    def test_blueprint(self):
        app, limiter = self.build_app(global_limits = ["1/minute"])
        bp = Blueprint("main", __name__)
        @bp.route("/t1")
        def t1():
            return "test"

        @bp.route("/t2")
        @limiter.limit("10 per minute")
        def t2():
            return "test"
        app.register_blueprint(bp)

        with app.test_client() as cli:
            self.assertEqual(cli.get("/t1").status_code, 200)
            self.assertEqual(cli.get("/t1").status_code, 429)
            for i in range(0,10):
                self.assertEqual(cli.get("/t2").status_code, 200)
            self.assertEqual(cli.get("/t2").status_code, 429)

    def test_disabled_flag(self):
        app, limiter = self.build_app(
            config={"RATELIMIT_ENABLED": False},
            global_limits=["1/minute"]
        )
        @app.route("/t1")
        def t1():
            return "test"

        @app.route("/t2")
        @limiter.limit("10 per minute")
        def t2():
            return "test"

        with app.test_client() as cli:
            self.assertEqual(cli.get("/t1").status_code, 200)
            self.assertEqual(cli.get("/t1").status_code, 200)
            for i in range(0,10):
                self.assertEqual(cli.get("/t2").status_code, 200)
            self.assertEqual(cli.get("/t2").status_code, 200)

    def test_decorated_dynamic_limits(self):
        app, limiter = self.build_app({"X": "2 per second"}, global_limits=["1/second"])
        def request_context_limit():
            limits = {
                "127.0.0.1": "10 per minute",
                "127.0.0.2": "1 per minute"
            }
            remote_addr = (request.access_route and request.access_route[0]) or request.remote_addr or '127.0.0.1'
            limit = limits.setdefault(remote_addr, '1 per minute')
            return limit

        @app.route("/t1")
        @limiter.limit("20/day")
        @limiter.limit(lambda: current_app.config.get("X"))
        @limiter.limit(request_context_limit)
        def t1():
            return "42"

        @app.route("/t2")
        @limiter.limit(lambda: current_app.config.get("X"))
        def t2():
            return "42"

        R1 = {"X_FORWARDED_FOR": "127.0.0.1, 127.0.0.0"}
        R2 = {"X_FORWARDED_FOR": "127.0.0.2"}

        with app.test_client() as cli:
            with hiro.Timeline().freeze() as timeline:
                for i in range(0,10):
                    self.assertEqual(cli.get("/t1", headers=R1).status_code, 200)
                    timeline.forward(1)
                self.assertEqual(cli.get("/t1", headers=R1).status_code, 429)
                self.assertEqual(cli.get("/t1", headers=R2).status_code, 200)
                self.assertEqual(cli.get("/t1", headers=R2).status_code, 429)
                timeline.forward(60)
                self.assertEqual(cli.get("/t1", headers=R2).status_code, 200)
                self.assertEqual(cli.get("/t2").status_code, 200)
                self.assertEqual(cli.get("/t2").status_code, 200)
                self.assertEqual(cli.get("/t2").status_code, 429)
                timeline.forward(1)
                self.assertEqual(cli.get("/t2").status_code, 200)

    def test_invalid_decorated_dynamic_limits(self):
        app = Flask(__name__)
        app.config.setdefault("X", "2 per sec")
        limiter = Limiter(app, global_limits=["1/second"])
        mock_handler = mock.Mock()
        mock_handler.level = logging.INFO
        limiter.logger.addHandler(mock_handler)
        @app.route("/t1")
        @limiter.limit(lambda: current_app.config.get("X"))
        def t1():
            return "42"

        with app.test_client() as cli:
            with hiro.Timeline().freeze() as timeline:
                self.assertEqual(cli.get("/t1").status_code, 200)
                self.assertEqual(cli.get("/t1").status_code, 429)
        # 2 for invalid limit, 1 for warning.
        self.assertEqual(mock_handler.handle.call_count, 3)
        self.assertTrue("failed to load ratelimit" in mock_handler.handle.call_args_list[0][0][0].msg)
        self.assertTrue("failed to load ratelimit" in mock_handler.handle.call_args_list[1][0][0].msg)
        self.assertTrue("exceeded at endpoint" in mock_handler.handle.call_args_list[2][0][0].msg)

    def test_invalid_decorated_static_limits(self):
        app = Flask(__name__)
        limiter = Limiter(app, global_limits=["1/second"])
        mock_handler = mock.Mock()
        mock_handler.level = logging.INFO
        limiter.logger.addHandler(mock_handler)
        @app.route("/t1")
        @limiter.limit("2/sec")
        def t1():
            return "42"

        with app.test_client() as cli:
            with hiro.Timeline().freeze() as timeline:
                self.assertEqual(cli.get("/t1").status_code, 200)
                self.assertEqual(cli.get("/t1").status_code, 429)
        self.assertTrue("failed to configure view function" in mock_handler.handle.call_args_list[0][0][0].msg)
        self.assertTrue("exceeded at endpoint" in mock_handler.handle.call_args_list[1][0][0].msg)


    def test_multiple_apps(self):
        app1 = Flask("app1")
        app2 = Flask("app2")

        limiter = Limiter(global_limits = ["1/second"])
        limiter.init_app(app1)
        limiter.init_app(app2)

        @app1.route("/ping")
        def ping():
            return "PONG"

        @app1.route("/slowping")
        @limiter.limit("1/minute")
        def slow_ping():
            return "PONG"


        @app2.route("/ping")
        @limiter.limit("2/second")
        def ping_2():
            return "PONG"

        @app2.route("/slowping")
        @limiter.limit("2/minute")
        def slow_ping_2():
            return "PONG"

        with hiro.Timeline().freeze() as timeline:
            with app1.test_client() as cli:
                self.assertEqual(cli.get("/ping").status_code, 200)
                self.assertEqual(cli.get("/ping").status_code, 429)
                timeline.forward(1)
                self.assertEqual(cli.get("/ping").status_code, 200)
                self.assertEqual(cli.get("/slowping").status_code, 200)
                timeline.forward(59)
                self.assertEqual(cli.get("/slowping").status_code, 429)
                timeline.forward(1)
                self.assertEqual(cli.get("/slowping").status_code, 200)
            with app2.test_client() as cli:
                self.assertEqual(cli.get("/ping").status_code, 200)
                self.assertEqual(cli.get("/ping").status_code, 200)
                self.assertEqual(cli.get("/ping").status_code, 429)
                timeline.forward(1)
                self.assertEqual(cli.get("/ping").status_code, 200)
                self.assertEqual(cli.get("/slowping").status_code, 200)
                timeline.forward(59)
                self.assertEqual(cli.get("/slowping").status_code, 200)
                self.assertEqual(cli.get("/slowping").status_code, 429)
                timeline.forward(1)
                self.assertEqual(cli.get("/slowping").status_code, 200)

    def test_headers_no_breach(self):
        app = Flask(__name__)
        limiter = Limiter(app, global_limits=["10/minute"], headers_enabled=True)
        @app.route("/t1")
        def t1():
            return "test"

        @app.route("/t2")
        @limiter.limit("2/second; 5 per minute; 10/hour")
        def t2():
            return "test"

        with hiro.Timeline().freeze() as timeline:
            with app.test_client() as cli:
                resp = cli.get("/t1")
                self.assertEqual(
                    resp.headers.get('X-RateLimit-Limit'),
                    '10'
                )
                self.assertEqual(
                    resp.headers.get('X-RateLimit-Remaining'),
                    '9'
                )
                self.assertEqual(
                    resp.headers.get('X-RateLimit-Reset'),
                    str(int(time.time() + 60))
                )
                resp = cli.get("/t2")
                self.assertEqual(
                    resp.headers.get('X-RateLimit-Limit'),
                    '2'
                )
                self.assertEqual(
                    resp.headers.get('X-RateLimit-Remaining'),
                    '1'
                )
                self.assertEqual(
                    resp.headers.get('X-RateLimit-Reset'),
                    str(int(time.time() + 1))
                )

    def test_headers_breach(self):
        app = Flask(__name__)
        limiter = Limiter(app, global_limits=["10/minute"], headers_enabled=True)

        @app.route("/t1")
        @limiter.limit("2/second; 10 per minute; 20/hour")
        def t():
            return "test"

        with hiro.Timeline().freeze() as timeline:
            with app.test_client() as cli:
                for i in range(11):
                    resp = cli.get("/t1")
                    timeline.forward(1)

                self.assertEqual(
                    resp.headers.get('X-RateLimit-Limit'),
                    '10'
                )
                self.assertEqual(
                    resp.headers.get('X-RateLimit-Remaining'),
                    '0'
                )
                self.assertEqual(
                    resp.headers.get('X-RateLimit-Reset'),
                    str(int(time.time() + 49))
                )

    def test_named_shared_limit(self):
        app, limiter = self.build_app()
        shared_limit_a = limiter.shared_limit("1/minute", scope='a')
        shared_limit_b = limiter.shared_limit("1/minute", scope='b')
        @app.route("/t1")
        @shared_limit_a
        def route1():
            return "route1"

        @app.route("/t2")
        @shared_limit_a
        def route2():
            return "route2"

        @app.route("/t3")
        @shared_limit_b
        def route3():
            return "route3"

        with hiro.Timeline().freeze() as timeline:
            with app.test_client() as cli:
                self.assertEqual(200, cli.get("/t1").status_code)
                self.assertEqual(200, cli.get("/t3").status_code)
                self.assertEqual(429, cli.get("/t2").status_code)

    def test_dynamic_shared_limit(self):
        app, limiter = self.build_app()
        fn_a = mock.Mock()
        fn_b = mock.Mock()
        fn_a.return_value = "foo"
        fn_b.return_value = "bar"


        dy_limit_a = limiter.shared_limit("1/minute", scope=fn_a)
        dy_limit_b = limiter.shared_limit("1/minute", scope=fn_b)


        @app.route("/t1")
        @dy_limit_a
        def t1():
            return "route1"

        @app.route("/t2")
        @dy_limit_a
        def t2():
            return "route2"

        @app.route("/t3")
        @dy_limit_b
        def t3():
            return "route3"

        with hiro.Timeline().freeze():
            with app.test_client() as cli:
                self.assertEqual(200, cli.get("/t1").status_code)
                self.assertEqual(200, cli.get("/t3").status_code)
                self.assertEqual(429, cli.get("/t2").status_code)
                self.assertEqual(429, cli.get("/t3").status_code)
                self.assertEqual(2, fn_a.call_count)
                self.assertEqual(2, fn_b.call_count)
                fn_b.assert_called_with("t3")
                fn_a.assert_has_calls([mock.call("t1"), mock.call("t2")])


    def test_whitelisting(self):

        app = Flask(__name__)
        limiter = Limiter(app, global_limits=["1/minute"], headers_enabled=True)

        @app.route("/")
        def t():
            return "test"

        @limiter.request_filter
        def w():
            if request.headers.get("internal", None) == "true":
                return True
            return False

        with hiro.Timeline().freeze() as timeline:
            with app.test_client() as cli:
                self.assertEqual(cli.get("/").status_code, 200)
                self.assertEqual(cli.get("/").status_code, 429)
                timeline.forward(60)
                self.assertEqual(cli.get("/").status_code, 200)

                for i in range(0,10):
                    self.assertEqual(
                        cli.get("/", headers = {"internal": "true"}).status_code,
                        200
                    )
