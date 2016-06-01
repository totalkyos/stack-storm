import copy
import functools
import os
import six
import sys

import jinja2
import routes
from swagger_spec_validator.validator20 import validate_spec
import yaml
from webob import exc, Request

from st2common import log as logging

LOG = logging.getLogger(__name__)


def op_resolver(op_id):
    module_name, func_name = op_id.split(':', 1)
    __import__(module_name)
    module = sys.modules[module_name]
    return functools.reduce(getattr, func_name.split('.'), module)


class Router(object):
    def __init__(self, arguments=None, spec_path='', debug=False):
        self.debug = debug

        self.arguments = arguments or {}
        self.spec_path = spec_path

        self.spec = {}
        self.routes = None

    def add_spec(self, spec_file, arguments=None):
        LOG.debug('Adding API: %s', spec_file)

        arguments = arguments or dict()
        arguments = dict(self.arguments, **arguments)

        yaml_path = os.path.join(self.spec_path, spec_file)

        LOG.debug('Loading specification: %s', yaml_path,
                  extra={'spec_yaml': yaml_path,
                         'arguments': arguments})

        with open(yaml_path, 'r') as yaml_file:
            spec_template = yaml_file.read()

        spec_string = jinja2.Template(spec_template).render(**arguments)
        spec = yaml.load(spec_string)

        validate_spec(copy.deepcopy(spec))

        self.spec = spec
        self.routes = routes.Mapper()

        for (path, methods) in six.iteritems(spec['paths']):
            for (method, endpoint) in six.iteritems(methods):
                conditions = {
                    'method': [method.upper()]
                }
                self.routes.connect(path, _api_path=path, _api_method=method, conditions=conditions)

        for route in self.routes.matchlist:
            LOG.debug('Route registered: %s %s', route.routepath, route.conditions)

    def __call__(self, req):
        """Invoke router as a view."""
        if self.routes is None:
            raise exc.HTTPInternalServerError(detail='Router has not been properly initialized')

        match = self.routes.match(req.path, req.environ)

        if match is None:
            raise exc.HTTPNotFound()

        # To account for situation when match may return multiple values
        try:
            path_vars = match[0]
        except KeyError:
            path_vars = match

        path = path_vars.pop('_api_path')
        method = path_vars.pop('_api_method')
        endpoint = self.spec['paths'][path][method]
        func = op_resolver(endpoint['operationId'])
        kw = {}

        for param in endpoint['parameters'] + endpoint.get('x-parameters', []):
            name = param['name']
            type = param['in']
            required = param.get('required', False)

            if type == 'query':
                kw[name] = req.GET.get(name)
            elif type == 'path':
                kw[name] = path_vars[name]
            elif type == 'header':
                kw[name] = req.headers.get(name)
            elif type == 'body':
                kw[name] = req.json
            elif type == 'formData':
                kw[name] = req.POST.get(name)
            elif type == 'environ':
                kw[name] = req.environ.get(name.upper())

            if required and not kw[name]:
                detail = 'Required parameter "%s" is missing' % name
                raise exc.HTTPBadRequest(detail=detail)

        resp = func(**kw)

        if resp is not None:
            return resp

    def as_wsgi(self, environ, start_response):
        """Invoke router as an wsgi application."""
        req = Request(environ)
        resp = self(req)
        return resp(environ, start_response)
