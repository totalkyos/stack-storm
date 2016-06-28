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

from six.moves import http_client
from oslo_config import cfg
from webob import exc, Response

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

    def _handle_proxy_auth(self, request, remote_user, **kwargs):
        remote_addr = kwargs.get('x-forwarded-for', kwargs.get('remote_addr'))
        extra = {'remote_addr': remote_addr}

        if remote_user:
            ttl = request.get('ttl', None)
            try:
                token = self._create_token_for_user(username=remote_user, ttl=ttl)
            except TTLTooLargeException as e:
                raise exc.HTTPBadRequest(detail=e.message)
            return self._process_successful_response(token=token)

        LOG.audit('Access denied to anonymous user.', extra=extra)
        self._abort_unauthorized()

    def _handle_standalone_auth(self, request, authorization, **kwargs):
        auth_backend = self._auth_backend.__class__.__name__
        remote_addr = kwargs.get('remote_addr')
        extra = {'auth_backend': auth_backend, 'remote_addr': remote_addr}

        if not authorization:
            LOG.audit('Authorization header not provided', extra=extra)
            self._abort_unauthorized()
            return

        auth_type, auth_value = authorization.split(' ')
        if auth_type.lower() not in ['basic']:
            extra['auth_type'] = auth_type
            LOG.audit('Unsupported authorization type: %s' % (auth_type), extra=extra)
            self._abort_unauthorized()
            return

        try:
            auth_value = base64.b64decode(auth_value)
        except Exception:
            LOG.audit('Invalid authorization header', extra=extra)
            self._abort_unauthorized()
            return

        split = auth_value.split(':')
        if len(split) != 2:
            LOG.audit('Invalid authorization header', extra=extra)
            self._abort_unauthorized()
            return

        username, password = split

        result = self._auth_backend.authenticate(username=username, password=password)

        if result is True:
            ttl = request.get('ttl', None)
            try:
                token = self._create_token_for_user(username=username, ttl=ttl)
                return self._process_successful_response(token=token)
            except TTLTooLargeException as e:
                raise exc.HTTPBadRequest(detail=e.message)

        LOG.audit('Invalid credentials provided', extra=extra)
        self._abort_unauthorized()

    def _abort_unauthorized(self):
        raise exc.HTTPUnauthorized(detail='Invalid or missing credentials')

    def _process_successful_response(self, token):
        resp = Response(json_encode(token),
                        content_type='application/json',
                        status=http_client.CREATED)
        resp.headers['X-API-URL'] = cfg.CONF.auth.api_url
        return resp

    def _create_token_for_user(self, username, ttl=None):
        tokendb = create_token(username=username, ttl=ttl)
        return TokenAPI.from_model(tokendb)

token_controller = TokenController()
