import os
import json
import pickle
from enum import Enum

import redis
from flask import Flask, request, Response
from flask_restful import Resource, Api

METHOD_NOT_ALLOWED_RESPONSE = {
    'body': {'message': 'Method not implemented'},
    'headers': {'Content-Type': 'application/json'},
    'status_code': 405
}

PREFLIGHT_RESPONSE = {
    'headers': {'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Methods': 'POST, GET, PUT, DELETE, OPTIONS',
                'Access-Control-Allow-Headers': '*',
                'Access-Control-Max-Age': '86400'},
    'status_code': 200
}


def load_json(file_path):
    with open(file_path) as f:
        data = json.load(f)
    return data


def save_json(file_path, data):
    with open(file_path, 'w') as f:
        json.dump(data, f)


class MethodFile(Enum):
    GET = 'get.json'
    POST = 'post.json'
    PUT = 'put.json'
    DELETE = 'delete.json'
    OPTIONS = 'options.json'


class Config:
    def __init__(self):
        self.mock_port = int(os.getenv('MOCK_PORT', 8080))
        self.responses_repository = os.getenv('RESPONSES_REPOSITORY', 'file_storage')
        self.mock_endpoints = os.getenv('MOCK_ENDPOINTS', 'endpoints.json')
        self.mock_workdir = os.getenv('MOCK_WORKDIR')
        self.endpoints_file = os.path.join(self.mock_workdir, self.mock_endpoints)
        responses_dir_name = os.getenv('MOCK_RESPONSES_DIR_NAME', 'responses')
        self.responses_dir = os.path.join(self.mock_workdir, responses_dir_name)
        if self.responses_repository == 'redis':
            self._build_redis_storage_config()

    def _build_redis_storage_config(self):
        self.responses_dir = ''
        self.redis_host = os.getenv('REDIS_HOST', 'localhost')
        self.redis_port = int(os.getenv('REDIS_PORT', 6379))
        self.redis_db = int(os.getenv('REDIS_DB', 0))


class BaseResponseReader:
    def get(self, response_path):
        pass

    def set(self, key, value):
        pass


class FileResponseReader:
    def get(self, response_path):
        with open(response_path) as f:
            data = json.load(f)
        return data

    def set(self, key, value):
        with open(key, 'w') as f:
            json.dump(value, f)


class RedisResponseReader:
    def __init__(self, config):
        self._reader = redis.StrictRedis(host=config.redis_host, port=config.redis_port, db=config.redis_db)

    def get(self, response_path):
        data = self._reader.get(response_path)
        if data is not None:
            data = pickle.loads(data)
        return data

    def set(self, key, value):
        self._reader.set(key, pickle.dumps(value))


class MockResource(Resource):
    _response_file_path = None
    _request_data = None
    _response = None
    _method = None
    _method_file = None

    def __init__(self, responses_path, endpoint_path, response_reader):
        self._responses_path = responses_path
        self._endpoint_path = endpoint_path
        self._response_reader = response_reader

    def get(self, **kwargs):
        self._process(**kwargs)
        return self._response

    def post(self, **kwargs):
        self._process(**kwargs)
        return self._response

    def put(self, **kwargs):
        self._process(**kwargs)
        return self._response

    def delete(self, **kwargs):
        self._process(**kwargs)
        return self._response

    def options(self, **kwargs):
        self._process(**kwargs)
        return self._response

    def _process(self, **kwargs):
        self._process_request(**kwargs)
        self._response = self._get_response()

    def _process_request(self, **kwargs):
        self._method = request.method
        self._method_file = MethodFile[self._method].value
        self._extract_request_data()
        self._update_file_paths(**kwargs)
        self._log_request_data()
        self._save_request_data()

    def _extract_request_data(self):
        request_data = {
            'headers': dict(request.headers),
            'body': request.json if request.is_json else request.data.decode() or None,
            'args': dict(request.args),
            'endpoint': request.endpoint,
            'method': self._method
        }
        self._request_data = request_data

    def _save_request_data(self):
        self._response_reader.set(self._request_file_path, self._request_data)

    def _log_request_data(self):
        app.logger.info("REQUEST: %s" % (self._request_data,))

    def _update_file_paths(self, **kwargs):
        endpoint_path = self._endpoint_path

        for key, value in kwargs.items():
            path_key = '<%s>' % key
            if key.startswith('__'):
                endpoint_path = endpoint_path.replace(path_key, key)
            else:
                endpoint_path = endpoint_path.replace(path_key, value)

        self._response_file_path = os.path.join(self._responses_path, endpoint_path, self._method_file)
        self._request_file_path = os.path.join(self._responses_path, 'last_request.json')

    def _get_response(self):
        try:
            response_data = self._response_reader.get(self._response_file_path)
        except IOError:
            response_data = None

        if response_data is None:
            response_data = self._get_predefined_response()

        body = response_data.get('body')
        status_code = response_data.get('status_code')
        headers = response_data.get('headers')
        app.logger.info("RESPONSE: %s" % (response_data,))
        response = Response(json.dumps(body), status_code, headers)
        return response

    def _get_predefined_response(self):
        if self._method_file == MethodFile.OPTIONS.value:
            response_data = PREFLIGHT_RESPONSE
        else:
            response_data = METHOD_NOT_ALLOWED_RESPONSE
        return response_data


if __name__ == '__main__':

    config = Config()

    app = Flask(__name__)
    api = Api(app)

    if config.responses_repository == 'file_storage':
        response_reader = FileResponseReader()
    else:
        response_reader = RedisResponseReader(config)

    resources = load_json(config.endpoints_file)

    for resource in resources:
        api.add_resource(MockResource, resource, endpoint=resource,
                         resource_class_kwargs={'responses_path': config.responses_dir,
                                                'endpoint_path': resource[1:],
                                                "response_reader": response_reader})

    app.run(debug=True, host='0.0.0.0', port=config.mock_port)
