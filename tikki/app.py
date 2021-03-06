# -*- coding: utf-8 -*-
"""Tikki backend

This module serves the RESTful interface required by the Tikki application.
"""
import datetime
import logging
from typing import Any, Dict

from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_jwt_simple import (JWTManager, create_jwt, get_jwt_identity,
                              jwt_optional, jwt_required)
import requests

from tikki import utils
from tikki.db import api as db_api
from tikki.db import metadata as db_metadata
from tikki.db.tables import Event, Record, RecordType, User, UserEventLink
from tikki.exceptions import AppException, FlaskRequestException
from tikki.version import get_version

# basic initialization
app = Flask(utils.APP_NAME)
utils.init_app(app)
log = logging.getLogger(utils.APP_NAME)
db_api.init(app)
jwt = JWTManager(app)
CORS(app)


def get_obj_type(path):
    if path == '/user':
        return User
    elif path == '/record':
        return Record
    elif path == '/event':
        return Event
    elif path == 'user-event-link':
        return UserEventLink


@jwt.jwt_data_loader
def add_claims_to_access_token(identity):
    return {
        'exp': identity['exp'],
        'iat': identity['iat'],
        'nbf': identity['iat'],
        'sub': identity['sub'],
        'rol': identity['rol']
    }


@app.route('/login', methods=['POST'])
def login():
    app.logger.debug(request)
    token = utils.get_args(request.json, required={'token': str})['token']
    user_payload: Dict[str, Any] = {}
    try:
        if "." not in token:
            # "new" opaque token
            response = requests.get(
                url="https://tikkifi.eu.auth0.com/userinfo",
                headers={"Authorization": f"Bearer {token}"},
            )
            body = response.json()
            if 'email' in body:
                user_payload["email"] = body["email"]
            if 'name' in body:
                user_payload["name"] = body["name"]
            username = body["sub"]
        else:
            # legacy token
            utils.flask_validate_request_is_json(request)
            payload = utils.get_auth0_payload(app, request)
            username = payload['sub']

        user = db_api.get_row(User, {'username': username})
        if not user:
            uuid = str(utils.generate_uuid())
            user_payload = {
                "id": uuid,
                "username": username,
                "payload": {},
                "type_id": 1
            }
            user = db_api.add_row(User, user_payload)

        identity = utils.create_jwt_identity(user)
        return utils.flask_return_success(
            {
                'jwt': create_jwt(identity),
                'user': user_payload if user_payload else user.json_dict
            }
        )
    except Exception as e:
        return utils.flask_handle_exception(e)


@app.route('/user', methods=['DELETE'], strict_slashes=False)
@app.route('/record', methods=['DELETE'], strict_slashes=False)
@app.route('/event', methods=['DELETE'], strict_slashes=False)
@app.route('/user-event-link', methods=['DELETE'], strict_slashes=False)
@jwt_required
def delete_record():
    try:
        # Check object type based on endpoint and define filters accordingly.

        obj_type = get_obj_type(request.path)
        required_args = {}
        if obj_type is UserEventLink:
            required_args['event_id'] = str
        else:
            required_args['id'] = str

        filters = utils.get_args(received=request.args,
                                 required=required_args,
                                 )
        filters['user_id'] = get_jwt_identity()
        db_api.delete_row(obj_type, filters)
        return utils.flask_return_success('OK')
    except Exception as e:
        return utils.flask_handle_exception(e)


@app.route('/uuid', methods=['GET'], strict_slashes=False)
def get_uuid():
    try:
        args = utils.get_args(received=request.args,
                              defaultable={'count': 1},
                              )
        count = args['count']
        if 0 < count <= 1024:
            return utils.flask_return_success(utils.generate_uuid(count))
        else:
            return utils.flask_return_exception('The count parameter cannot be below 1 '
                                                'or greater than 1024.', 400)
    except Exception as e:
        return utils.flask_handle_exception(e)


@app.route('/whoami', methods=['GET'], strict_slashes=False)
@jwt_optional
def get_whoami():
    try:

        user = get_jwt_identity()
        if user is None:
            return utils.flask_return_success('Nobody')
        else:
            return utils.flask_return_success(user)
    except Exception as e:
        return utils.flask_handle_exception(e)


@app.route('/schema', methods=['GET'], strict_slashes=False)
@jwt_optional
def get_schema():
    log.info('schema')
    try:
        jwt_id = get_jwt_identity()
        type_dict = dict()
        if jwt_id is not None:
            filters = {'user_id': str(jwt_id)}
            records = db_api.get_rows(Record, filters)

            # sort records based on user_id and creation date to pick most recent
            # result per user
            # TODO: sort in db.get_rows

            records.sort(key=lambda x: (x.type_id, x.created_at), reverse=True)
            lag_type_id = None
            for record in records:
                if lag_type_id != record.type_id:
                    lag_type_id = record.type_id
                    type_dict[record.type_id] = record

        rows = db_api.get_rows(RecordType, {})
        result_list = list()
        for row in rows:
            result = row.json_dict
            result['ask'] = 1 if jwt_id is not None and row.category_id == 2 and \
                row.id not in type_dict else 0
            result_list.append(result)
        return utils.flask_return_success(result_list)
    except Exception as ex:
        return utils.flask_handle_exception(ex)


@app.route('/user', methods=['GET'], strict_slashes=False)
@jwt_required
def get_user():
    filters = utils.get_args(received=request.args,
                             optional={'id': str, 'username': str},
                             )
    try:
        users = db_api.get_rows(User, filters)
        return utils.flask_return_success([i.json_dict for i in users])
    except Exception as e:
        return utils.flask_handle_exception(e)


@app.route('/user', methods=['POST'], strict_slashes=False)
def post_user():
    try:
        utils.flask_validate_request_is_json(request)
        payload = utils.get_auth0_payload(app, request)
        now = datetime.datetime.now()
        uuid = str(utils.generate_uuid())
        in_user = utils.get_args(received=request.json,
                                 defaultable={'id': uuid, 'created_at': now,
                                              'updated_at': now, 'payload': {}},
                                 constant={'type_id': 1},
                                 )
        in_user['username'] = payload['sub']
        user = db_api.add_row(User, in_user)
        identity = utils.create_jwt_identity(user, payload)
        return utils.flask_return_success({'jwt': create_jwt(identity),
                                          'user': user.json_dict})
    except Exception as e:
        return utils.flask_handle_exception(e)


@app.route('/user', methods=['PUT'], strict_slashes=False)
@jwt_required
def put_user():
    try:
        utils.flask_validate_request_is_json(request)
        now = datetime.datetime.now()
        in_user = utils.get_args(received=request.json,
                                 defaultable={'created_at': now, 'updated_at': now,
                                              'payload': {}})
        filters = {'id': get_jwt_identity()}
        user = db_api.update_row(User, filters, in_user)
        return utils.flask_return_success(user.json_dict)
    except Exception as e:
        return utils.flask_handle_exception(e)


@app.route('/user', methods=['PATCH'], strict_slashes=False)
@jwt_required
def patch_user():
    try:
        utils.flask_validate_request_is_json(request)
        now = datetime.datetime.now()
        in_user = utils.get_args(received=request.json,
                                 required={'id': str},
                                 defaultable={'updated_at': now},
                                 optional={'created_at': datetime.datetime,
                                           'payload': dict})
        filters = {'id': in_user.pop('id', None)}
        user = db_api.update_row(User, filters, in_user)
        return utils.flask_return_success(user.json_dict)
    except Exception as e:
        return utils.flask_handle_exception(e)


@app.route('/test/cooperstest/compstat', methods=['GET'], strict_slashes=False)
@jwt_required
def get_cooperstest_compstat():
    try:
        user_id = get_jwt_identity()
        filters = {'type_id': int(db_metadata.RecordTypeEnum.COOPERS_TEST)}
        records = db_api.get_rows(Record, filters)

        # sort records based on user_id and creation date to pick most recent
        # result per user
        # TODO: do sorting in db.get_rows
        records.sort(key=lambda x: (x.user_id, x.created_at), reverse=True)
        filtered_records = list()
        lag_user_id = None
        user_record = None
        for record in records:
            if lag_user_id != record.user_id:
                lag_user_id = record.user_id
                filtered_records.append(record)
                if user_id == str(record.user_id):
                    user_record = record

        # sort filtered records and get index for quantile calculation
        try:
            index = sorted(filtered_records,
                           key=lambda x: x.payload['distance']).index(user_record)
        except ValueError:
            index = None

        if index is None or len(filtered_records) == 0:
            quantile = 0
        else:
            quantile = (index + 1) / len(filtered_records)
        return utils.flask_return_success({'quantile': quantile})

    except Exception as e:
        return utils.flask_return_exception(e, 500)


@app.route('/test/pushup60test/compstat', methods=['GET'], strict_slashes=False)
@jwt_required
def get_pushup60test_compstat():
    try:
        user_id = get_jwt_identity()
        if user_id is None:
            return jsonify({'message': 'Undefined user id.'}), 400
        filters = {'type_id': int(db_metadata.RecordTypeEnum.PUSH_UP_60_TEST)}
        records = db_api.get_rows(Record, filters)

        # sort records based on user_id and creation date to pick most recent
        # result per user
        # TODO: sort in db.get_rows

        records.sort(key=lambda x: (x.user_id, x.created_at), reverse=True)
        filtered_records = list()
        lag_user_id = None
        user_record = None
        for record in records:
            if lag_user_id != record.user_id:
                lag_user_id = record.user_id
                filtered_records.append(record)
                if user_id == str(record.user_id):
                    user_record = record

        # sort filtered records and get index for quantile calculation
        filtered_records.sort(key=lambda x: x.payload['pushups'])
        try:
            index = filtered_records.index(user_record)
        except ValueError:
            index = None

        if index is None or len(filtered_records) == 0:
            quantile = 0
        else:
            quantile = (index + 1) / len(filtered_records)
        return jsonify({'result': {'quantile': quantile}}), 200

    except Exception as e:
        return utils.flask_return_exception(e, 500)


@app.route('/record', methods=['GET'], strict_slashes=False)
@jwt_required
def get_record():
    filters = utils.get_args(
        received=request.args,
        constant={'user_id': get_jwt_identity()},
        optional={'id': str, 'event_id': str, 'type_id': int},
    )
    try:
        rows = db_api.get_rows(Record, filters)
        return utils.flask_return_success([row.json_dict for row in rows])
    except Exception as e:
        return utils.flask_handle_exception(e)


@app.route('/record', methods=['POST'], strict_slashes=False)
@jwt_required
def post_record():
    try:
        utils.flask_validate_request_is_json(request)
        now = datetime.datetime.now()
        uuid = str(utils.generate_uuid())
        row = utils.get_args(
            received=request.json,
            optional={'event_id': str},
            constant={
                'user_id': get_jwt_identity(),
            },
            defaultable={
                'id': uuid,
                'created_at': now,
                'updated_at': now,
                'payload': {},
                'type_id': 0,
            }
        )

        # Add created_user which defaults to the user_id, merging it with the
        # main row object.
        user_id = row['user_id']
        created_user = utils.get_args(received=request.json,
                                      defaultable={'created_user_id': user_id},
                                      )
        row.update(created_user)

        # And finally add details of who validated the record and when if provided.
        validated = utils.get_args(received=request.json,
                                   defaultable={'validated_at': now},
                                   optional={'validated_user_id': str},
                                   )
        if 'validated_user_id' in validated:
            row.update(validated)

        record = db_api.add_row(Record, row)
        return utils.flask_return_success(record.json_dict)
    except Exception as e:
        return utils.flask_handle_exception(e)


@app.route('/record', methods=['PATCH'], strict_slashes=False)
@jwt_required
def patch_record():
    try:
        utils.flask_validate_request_is_json(request)
        now = datetime.datetime.now()
        row = utils.get_args(received=request.json,
                             required={'id': str},
                             defaultable={'updated_at': now},
                             optional={'created_at': datetime.datetime,
                                       'payload': dict, 'type_id': int,
                                       'event_id': str},
                             )

        # Add created_user which defaults to the user_id, merging it with the
        # main row object.
        user = get_jwt_identity()

        created_user = utils.get_args(received=request.json,
                                      defaultable={'created_user': user},
                                      )
        row.update(created_user)

        # And finally add details of who validated the record and when if provided.
        validated = utils.get_args(received=request.json,
                                   defaultable={'validated_at': now},
                                   optional={'validated_user_id': str},
                                   )
        if validated.get('validated_user_id') is not None:
            row.update(validated)

        filters = {'id': row.pop('id', None)}
        record = db_api.update_row(Record, filters, row)
        return utils.flask_return_success(record.json_dict)
    except Exception as e:
        return utils.flask_handle_exception(e)


@app.route('/record', methods=['PUT'], strict_slashes=False)
@jwt_required
def put_record():
    try:
        utils.flask_validate_request_is_json(request)
        now = datetime.datetime.now()
        uuid = str(utils.generate_uuid())
        user = get_jwt_identity()
        row = utils.get_args(received=request.json,
                             defaultable={'id': uuid, 'created_at': now,
                                          'updated_at': now, 'payload': {},
                                          'type_id': 0,
                                          'user_id': user},
                             optional={'event_id': str},
                             )
        filters = {'id': row.pop('id', None)}

        # Add created_user which defaults to the user_id, merging it with the
        # main row object.
        created_user = utils.get_args(received=request.json,
                                      defaultable={'created_user': user},
                                      )
        row.update(created_user)

        # And finally add details of who validated the record and when if provided.
        validated = utils.get_args(received=request.json,
                                   defaultable={'validated_at': now},
                                   optional={'validated_user_id': str},
                                   )
        if validated.get('validated_user_id') is not None:
            row.update(validated)

        record = db_api.update_row(Record, filters, row)
        return utils.flask_return_success(record.json_dict)
    except Exception as e:
        return utils.flask_handle_exception(e)


@app.route('/event', methods=['GET'], strict_slashes=False)
@jwt_required
def get_event():
    filters = utils.get_args(received=request.args,
                             optional={'id': str, 'user_id': str, 'type_id': int},
                             )
    try:
        rows = db_api.get_rows(Event, filters)
        return utils.flask_return_success([row.json_dict for row in rows])
    except Exception as e:
        return utils.flask_handle_exception(e)


@app.route('/event', methods=['POST'], strict_slashes=False)
@jwt_required
def post_event():
    try:
        utils.flask_validate_request_is_json(request)
        now = datetime.datetime.now()
        uuid = str(utils.generate_uuid())
        user = get_jwt_identity()
        row = utils.get_args(received=request.json,
                             required={'name': str, 'description': str,
                                       'address': str, 'postal_code': str,
                                       'event_at': datetime.datetime},
                             defaultable={'id': uuid, 'created_at': now,
                                          'updated_at': now, 'payload': {},
                                          'organization_id': 0, 'user_id': user},
                             )
        event = db_api.add_row(Event, row)
        return utils.flask_return_success(event.json_dict)
    except Exception as e:
        return utils.flask_handle_exception(e)


@app.route('/event', methods=['PUT'], strict_slashes=False)
@jwt_required
def put_event():
    try:
        utils.flask_validate_request_is_json(request)
        now = datetime.datetime.now()
        uuid = str(utils.generate_uuid())
        user = get_jwt_identity()
        row = utils.get_args(received=request.json,
                             required={'name': str, 'description': str,
                                       'address': str, 'postal_code': str,
                                       'event_at': datetime.datetime},
                             defaultable={'id': uuid, 'created_at': now,
                                          'updated_at': now, 'payload': {},
                                          'organization_id': 0, 'user_id': user},
                             )

        filters = {'id': row.pop('id', None)}
        event = db_api.update_row(Event, filters, row)
        return utils.flask_return_success(event.json_dict)
    except Exception as e:
        return utils.flask_handle_exception(e)


@app.route('/user-event-link', methods=['GET'], strict_slashes=False)
@jwt_required
def get_user_event_link():
    filters = utils.get_args(received=request.args,
                             optional={'user_id': str, 'event_id': str},
                             )
    try:
        rows = db_api.get_rows(UserEventLink, filters)
        return utils.flask_return_success([row.json_dict for row in rows])
    except Exception as e:
        return utils.flask_handle_exception(e)


@app.route('/user-event-link', methods=['POST'], strict_slashes=False)
@jwt_required
def post_user_event_link():
    try:
        utils.flask_validate_request_is_json(request)
        now = datetime.datetime.now()
        user = get_jwt_identity()
        row = utils.get_args(received=request.json,
                             required={'event_id': str},
                             defaultable={'created_at': now, 'updated_at': now,
                                          'user_id': user, 'payload': {}},
                             )

        obj = db_api.add_row(UserEventLink, row)
        return utils.flask_return_success(obj.json_dict)
    except (AppException, FlaskRequestException) as e:
        return utils.flask_handle_exception(e)


@app.route("/")
def hello():
    return f'Greetings from the Tikki API (v. {get_version()})'


@app.route("/test", methods=['GET'])
@jwt_optional
def test():
    try:
        args = utils.get_args(received=request.args, required={'type': str})
        if args['type'] == 'error':
            log.error(f'!! {request}')
        elif args['type'] == 'warning':
            log.warning(request)
        elif args['type'] == 'info':
            log.info(request)
        elif args['type'] == 'debug':
            log.debug(request)
        return utils.flask_return_success(args)
    except (AppException, FlaskRequestException) as e:
        log.error(request)
        return utils.flask_handle_exception(e)


if __name__ == "__main__":
    app.run()
