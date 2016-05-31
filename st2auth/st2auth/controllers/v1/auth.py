# Licensed to the StackStorm, Inc ('StackStorm') under one or more
# contributor license agreements.  See the NOTICE file distributed with
# this work for additional information regarding copyright ownership.
# The ASF licenses this file to You under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance with
# the License.  You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import base64

import flask
from six.moves import http_client
from oslo_config import cfg

from st2common.exceptions.auth import TTLTooLargeException
from st2common.models.api.auth import TokenAPI
from st2common.services.access import create_token
from st2common.util.jsonify import json_encode
from st2common import log as logging
from st2auth.backends import get_backend_instance


LOG = logging.getLogger(__name__)


class TokenController(object):
    def __init__(self, *args, **kwargs):
        super(TokenController, self).__init__(*args, **kwargs)

        if cfg.CONF.auth.mode == 'standalone':
            self._auth_backend = get_backend_instance(name=cfg.CONF.auth.backend)
        else:
            self._auth_backend = None

    def post(self, request, **kwargs):
        if cfg.CONF.auth.mode == 'proxy':
            return self._handle_proxy_auth(request=request, **kwargs)
        elif cfg.CONF.auth.mode == 'standalone':
            return self._handle_standalone_auth(request=request, **kwargs)

    def _handle_proxy_auth(self, request, **kwargs):
        remote_addr = flask.request.headers.get('x-forwarded-for', flask.request.remote_addr)
        extra = {'remote_addr': remote_addr}

        if flask.request.remote_user:
            ttl = getattr(request, 'ttl', None)
            try:
                token = self._create_token_for_user(username=flask.request.remote_user, ttl=ttl)
            except TTLTooLargeException as e:
                self._abort_request(status_code=http_client.BAD_REQUEST,
                                    message=e.message)
            return self._process_successful_response(token=token)

        LOG.audit('Access denied to anonymous user.', extra=extra)
        self._abort_request()

    def _handle_standalone_auth(self, request, **kwargs):
        authorization = flask.request.authorization

        auth_backend = self._auth_backend.__class__.__name__
        remote_addr = flask.request.remote_addr
        extra = {'auth_backend': auth_backend, 'remote_addr': remote_addr}

        if not authorization:
            LOG.audit('Authorization header not provided', extra=extra)
            self._abort_request()
            return

        result = self._auth_backend.authenticate(username=authorization.username,
                                                 password=authorization.password)
        if result is True:
            ttl = getattr(request, 'ttl', None)
            try:
                token = self._create_token_for_user(username=authorization.username, ttl=ttl)
                return self._process_successful_response(token=token)
            except TTLTooLargeException as e:
                self._abort_request(status_code=http_client.BAD_REQUEST,
                                    message=e.message)
                return

        LOG.audit('Invalid credentials provided', extra=extra)
        self._abort_request()

    def _abort_request(self, status_code=http_client.UNAUTHORIZED,
                       message='Invalid or missing credentials'):
        flask.abort(status_code, message)

    def _process_successful_response(self, token):
        api_url = cfg.CONF.auth.api_url
        resp = flask.Response(json_encode(token))
        resp.headers['X-API-URL'] = api_url
        return resp

    def _create_token_for_user(self, username, ttl=None):
        tokendb = create_token(username=username, ttl=ttl)
        return TokenAPI.from_model(tokendb)

token_controller = TokenController()
