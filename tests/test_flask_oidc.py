from pkg_resources import resource_filename, resource_stream
import json
import time
import codecs
from base64 import urlsafe_b64encode

try:
    from unittest.mock import Mock, patch, MagicMock
except ImportError:
    from mock import Mock, patch

from six.moves.urllib.parse import urlsplit, parse_qs, urlencode
from nose.tools import nottest

from .app import create_app, configure_mock_object_version1, configure_mock_version2, _configure_mock_object, \
    callback_method, configure_mock_version3

last_request = None
with resource_stream(__name__, 'client_secrets.json') as f:
    client_secrets = json.load(codecs.getreader('utf-8')(f))


class MockHttpResponse(object):
    status = 200


class MockHttp(object):
    def request(self, path, method='GET', post_string='', **kwargs):
        global last_request
        last_request = kwargs
        last_request['path'] = path
        iat = time.time() - 1
        exp = time.time() + 1
        post_args = {}
        if method == 'POST':
            post_args = parse_qs(post_string)

        if path == 'https://test/token':
            return MockHttpResponse(), json.dumps({
                'access_token': 'mock_access_token',
                'refresh_token': 'mock_refresh_token',
                'id_token': '.{0}.'.format(urlsafe_b64encode(json.dumps({
                    'aud': client_secrets['web']['client_id'],
                    'sub': 'mock_user_id',
                    'email_verified': True,
                    'iat': iat,
                    'exp': exp,
                    'iss': 'accounts.google.com',
                }).encode('utf-8')).decode('utf-8')),
            }).encode('utf-8')
        elif path == 'https://test/tokeninfo':
            assert post_args['client_id'] == ['MyClient'], \
                'Client ID is specified'
            req_token = post_args['token'][0]
            token_info = {'active': False}
            if req_token in ['query_token', 'post_token']:
                token_info['active'] = True
                token_info['scope'] = 'openid'
                token_info['sub'] = 'valid_sub'
                token_info['aud'] = 'MyClient'
            elif req_token == 'insufficient_token':
                token_info['active'] = True
                token_info['scope'] = 'email'
                token_info['sub'] = 'valid_sub'
                token_info['aud'] = 'MyClient'
            elif req_token == 'multi_aud_token':
                token_info['active'] = True
                token_info['scope'] = 'openid'
                token_info['sub'] = 'valid_sub'
                token_info['aud'] = ['MyClient', 'TheirClient']
            elif req_token == 'some_elses_token':
                token_info['active'] = True
                token_info['scope'] = 'openid'
                token_info['sub'] = 'valid_sub'
                token_info['aud'] = 'TheirClient'
            return MockHttpResponse(), json.dumps(token_info)
        elif path == "http://test/auth/realms/realm":
            token_info = {
                "realm": "realm",
                "public_key": "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAhc3J4GYSIsxBVUjjA0iI+ssP0fv4j2h9wyBXxMWsLk94xZ0+OxKZiPD8kuLhnO29WFdaCleUGjY7+rnl6XhHIVoMSWYkUELE3JWTiAFWkQQyNIxcYvaS/qz41astgmRAdZ5+qlU+QEPirX4R5FpMUp2ZEn1sy18ngAGlYZneiCXNa6C6ktLQ/PY0qxs8BRY51aH9dFfhfFko6/n8IRKOtqu+MuTJ3bNu7fhHGOmL8NjhcA54cHDNirltroWdwdfPWIuPTBjPsJ7y+ysde6AbjfMC9DJ8HOeVfAeL+J0Xn4TsYN9FdG8ZJgKmkjCzzNTKZwZ/YACdqs1bcS8fa0caXQIDAQAB",
                "token-service": "http://localhost/auth/realms/realm/protocol/openid-connect",
                "account-service": "http://localhost/auth/realms/realm/account",
                "tokens-not-before": 0
            }

            return MockHttpResponse(), json.dumps(token_info)
        else:
            raise Exception('Non-recognized path %s requested' % path)


@nottest
def make_test_app():
    """
    :return: A Flask test client for the test app, and the mocks it uses.
    """
    return create_app({
        'SECRET_KEY': 'SEEEKRIT',
        'TESTING': True,
        'OIDC_KEYCLOAK_ENABLED': True,
        'OIDC_KEYCLOAK_CLIENT_SECRETS': resource_filename(
            __name__, 'keycloak_authorization.json'),
        'OIDC_CLIENT_SECRETS': resource_filename(
            __name__, 'client_secrets.json'),
    }, {
    })


def callback_url_for(response):
    """
    Take a redirect to the IdP and turn it into a redirect from the IdP.
    :return: The URL that the IdP would have redirected the user to.
    """
    location = urlsplit(response.headers['Location'])
    query = parse_qs(location.query)
    state = query['state'][0]
    callback_url = '/oidc_callback?' \
                   + urlencode({'state': state, 'code': 'mock_auth_code'})
    return callback_url


@patch('time.time', Mock(return_value=time.time()))
@patch('httplib2.Http', MockHttp)
def test_signin():
    """
    Happy path authentication test.
    """
    test_client = make_test_app().test_client()

    # make an unauthenticated request,
    # which should result in a redirect to the IdP
    r1 = test_client.get('/')
    assert r1.status_code == 302, \
        "Expected redirect to IdP " \
        "(response status was {response.status})".format(response=r1)

    # the app should now contact the IdP
    # to exchange that auth code for credentials
    r2 = test_client.get(callback_url_for(r1))
    assert r2.status_code == 302, \
        "Expected redirect to destination " \
        "(response status was {response.status})".format(response=r2)
    r2location = urlsplit(r2.headers['Location'])
    assert r2location.path == '/', \
        "Expected redirect to destination " \
        "(unexpected path {location.path})".format(location=r2location)

    # Let's get the at and rt
    r3 = test_client.get('/at')
    assert r3.status_code == 200, \
        "Expected access token to succeed"
    page_text = ''.join(codecs.iterdecode(r3.response, 'utf-8'))
    assert page_text == 'mock_access_token', \
        "Access token expected"
    r4 = test_client.get('/rt')
    assert r4.status_code == 200, \
        "Expected refresh token to succeed"
    page_text = ''.join(codecs.iterdecode(r4.response, 'utf-8'))
    assert page_text == 'mock_refresh_token', \
        "Refresh token expected"


@patch('httplib2.Http', MockHttp)
def test_refresh():
    """
    Test token expiration and refresh.
    """
    test_client = make_test_app().test_client()

    with patch('time.time', Mock(return_value=time.time())) as time_1:
        # authenticate and get an ID token cookie
        auth_redirect = test_client.get('/')
        callback_redirect = test_client.get(callback_url_for(auth_redirect))
        actual_page = test_client.get(callback_redirect.headers['Location'])
        page_text = ''.join(codecs.iterdecode(actual_page.response, 'utf-8'))
        assert page_text == 'too many secrets', "Authentication failed"

    # app should now try to use the refresh token
    with patch('time.time', Mock(return_value=time.time() + 10)) as time_2:
        test_client.get('/')
        body = parse_qs(last_request['body'])
        assert body.get('refresh_token') == ['mock_refresh_token'], \
            "App should have tried to refresh credentials"


def _check_api_token_handling(api_path):
    """
    Test API token acceptance.
    """
    test_client = make_test_app().test_client()

    # Test without a token
    resp = test_client.get(api_path)
    assert resp.status_code == 401, "Token should be required"
    resp = json.loads(resp.get_data().decode('utf-8'))
    assert resp['error'] == 'invalid_token', "Token should be requested"

    # Test with invalid token
    resp = test_client.get(api_path + '?access_token=invalid_token')
    assert resp.status_code == 401, 'Token should be rejected'

    # Test with query token
    resp = test_client.get(api_path + '?access_token=query_token')
    assert resp.status_code == 200, 'Token should be accepted'
    resp = json.loads(resp.get_data().decode('utf-8'))
    assert resp['token']['sub'] == 'valid_sub'

    # Test with post token
    resp = test_client.post(api_path, data={'access_token': 'post_token'})
    assert resp.status_code == 200, 'Token should be accepted'

    # Test with insufficient token
    resp = test_client.post(api_path + '?access_token=insufficient_token')
    assert resp.status_code == 401, 'Token should be refused'
    resp = json.loads(resp.get_data().decode('utf-8'))
    assert resp['error'] == 'invalid_token'

    # Test with multiple audiences
    resp = test_client.get(api_path + '?access_token=multi_aud_token')
    assert resp.status_code == 200, 'Token should be accepted'

    # Test with token for another audience
    resp = test_client.get(api_path + '?access_token=some_elses_token')
    assert resp.status_code == 200, 'Token should be accepted'
    test_client.application.config['OIDC_RESOURCE_CHECK_AUD'] = True
    resp = test_client.get(api_path + '?access_token=some_elses_token')
    assert resp.status_code == 401, 'Token should be refused'


@patch('httplib2.Http', MockHttp)
def test_api_token():
    _check_api_token_handling('/api')


@patch('httplib2.Http', MockHttp)
def test_api_token_with_external_rendering():
    _check_api_token_handling('/external_api')


@patch('httplib2.Http', MockHttp)
def test_validate_token_return_false():
    test_app = make_test_app()

    no_token_err = test_app.oidc.validate_token(None)
    assert bool(no_token_err) is False, \
        "Expected no_token_err to eval to False"
    assert no_token_err == 'Token required but invalid', \
        "Expected correct no token error message"


@patch('httplib2.Http', MockHttp)
def test_authorization_allowed_with_valid_permissions():
    test_app = make_test_app()
    configure_mock_object_version1(test_app)

    test_client = test_app.test_client()

    r = test_client.get('/test1' + '?access_token=query_token')
    test_app.oidc.keycloakApi.authorize.assert_called_once_with("query_token")
    assert r.status_code == 200, "Request should be granted (response status was {response.status})".format(response=r)

    test_app = make_test_app()
    configure_mock_object_version1(test_app)

    test_client = test_app.test_client()

    r = test_client.get('/test2' + '?access_token=query_token')
    test_app.oidc.keycloakApi.authorize.assert_called_once_with("query_token")
    assert r.status_code == 200, "Request should be granted " \
                                 "(response status was {response.status})".format(response=r)

    test_app = make_test_app()
    configure_mock_version2(test_app)

    test_client = test_app.test_client()

    r = test_client.get('/test3' + '?access_token=query_token')
    test_app.oidc.keycloakApi.authorize.assert_called_once_with("query_token")

    assert r.status_code == 200, "Request should be granted " \
                                 "(response status was {response.status})".format(response=r)


@patch('httplib2.Http', MockHttp)
def test_authorization_denied_because_of_invalid_permissions():
    test_app = make_test_app()
    configure_mock_object_version1(test_app)

    test_client = test_app.test_client()

    r = test_client.get('/test3' + '?access_token=query_token')
    test_app.oidc.keycloakApi.authorize.assert_called_once_with("query_token")

    assert r.status_code == 403, "Request should be denied " \
                                 "(response status was {response.status})".format(response=r)


@patch('httplib2.Http', MockHttp)
def test_authorization_allowed_because_of_disabling_verification_of_permissions():
    test_app = make_test_app()
    _configure_mock_object(test_app)

    test_client = test_app.test_client()

    r = test_client.get('/unprotected' + '?access_token=query_token')
    test_app.oidc.keycloakApi.authorize.assert_not_called()
    assert r.status_code == 200, "Request should be denied " \
                                 "(response status was {response.status})".format(response=r)


@patch('httplib2.Http', MockHttp)
def test_authorization_denied_because_of_invalid_jwt_token():
    test_app = make_test_app()
    configure_mock_version3(test_app)

    test_client = test_app.test_client()

    r = test_client.get('/test3' + '?access_token=query_token')
    test_app.oidc.keycloakApi.authorize.assert_called_once_with("query_token")

    assert r.status_code == 403, "Request should be denied " \
                                 "(response status was {response.status})".format(response=r)


@patch('httplib2.Http', MockHttp)
def test_valid_call_of_custom_callback_method():
    test_app = make_test_app()
    configure_mock_version2(test_app)

    test_client = test_app.test_client()

    r = test_client.get('/test4' + '?access_token=query_token')
    callback_method.assert_called()
    assert r.status_code == 200, "Request should be granted " \
                                 "(response status was {response.status})".format(response=r)


@patch('httplib2.Http', MockHttp)
def test_verify_uri():
    test_app = make_test_app()
    test_app.oidc._set_current_uri("/version/of/assa/.bla")
    assert test_app.oidc._verify_uri("/version/*/.bla") is True

    test_app.oidc._set_current_uri("/version/of/assa/sss.bla")
    assert test_app.oidc._verify_uri("/version/*/*.bla") is True

    test_app.oidc._set_current_uri("/version/of/assa/sss.alba")
    assert test_app.oidc._verify_uri("/version/*/*.bla") is False

    test_app.oidc._set_current_uri("/version/sss.bla")
    assert test_app.oidc._verify_uri("/version/*/*.bla") is False

    test_app.oidc._set_current_uri("/version/")
    assert test_app.oidc._verify_uri("/version") is True
