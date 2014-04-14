"""
HTTP utils, such as formulation of URLs
"""

from itertools import chain
from urllib import quote, urlencode
import json

import treq

from otter.util.config import config_value


# REVIEW: I started with specific subclasses for each system (nova, clb and identity)
# but they had too much in common. So decided to put stuff in this class itself
class UpstreamError(Exception):
    """
    An upstream system error that wraps more detailed error

    :ivar Failure error: The detailed error being wrapped
    :ivar str system: the upstream system being contacted, eg: nova, clb, identity
    :ivar str operation: the operation being performed
    :ivar str url: some representation of the connection endpoint -
        e.g. a hostname or ip or a url
    """
    def __init__(self, error, system, operation, url=None):
        self.error = error
        self.system = system
        self.operation = operation
        self.url = url
        self.apierr_message = None
        msg = self.system + ' error: '
        if error.check(APIError):
            self._parse_message(error.value.body)
            msg += '{} - {}'.format(error.value.code, self.apierr_message)
        else:
            msg += str(error)
        super(UpstreamError, self).__init__(msg)

    def _parse_message(self, body):
        try:
            body = json.loads(body)
            self.apierr_message = body[body.keys()[0]]['message']
        except:
            self.apierr_message = 'Could not parse API error body'

    def details(self):
        """
        Return `dict` of all the details within this object
        """
        d = self.__dict__.copy()
        del d['error']
        if self.error.check(APIError):
            e = self.error.value
            d.update({'code': e.code, 'body': e.body, 'headers': e.headers})
        return d


def wrap_upstream_error(f, system, operation, url=None):
    """
    Wrap error in UpstreamError
    """
    raise UpstreamError(f, system, operation, url)


def raise_error_on_code(failure, code, error, system, operation, url):
    """
    Raise `error` if given `code` in APIError.code inside failure matches.
    Otherwise `RequestError` is raised with `url` and `data`
    """
    failure.trap(APIError)
    if failure.value.code == code:
        raise error
    raise UpstreamError(failure, system, operation, url)


def wrap_request_error(failure, target, data=None):
    """
    Some errors, such as connection timeouts, aren't useful becuase they don't
    contain the url that is timing out, so wrap the error in one that also has
    the url.
    """
    raise RequestError(failure, target, data)


def append_segments(uri, *segments):
    """
    Append segments to URI in a reasonable way.

    :param str or unicode uri: base URI with or without a trailing /.
        If uri is unicode it will be encoded as ascii.  This is not strictly
        correct but is probably fine since all these URIs are coming from JSON
        and should be properly encoded.  We just need to make them str objects
        for Twisted.
    :type segments: str or unicode
    :param segments: One or more segments to append to the base URI.

    :return: complete URI as str.
    """
    def _segments(segments):
        for s in segments:
            if isinstance(s, unicode):
                s = s.encode('utf-8')

            yield quote(s)

    if isinstance(uri, unicode):
        uri = uri.encode('ascii')

    uri = '/'.join(chain([uri.rstrip('/')], _segments(segments)))
    return uri


class APIError(Exception):
    """
    An error raised when a non-success response is returned by the API.

    :param int code: HTTP Response code for this error.
    :param str body: HTTP Response body for this error or None.
    :param Headers headers: HTTP Response headers for this error, or None
    """
    def __init__(self, code, body, headers=None):
        Exception.__init__(
            self,
            'API Error code={0!r}, body={1!r}, headers={2!r}'.format(
                code, body, headers))

        self.code = code
        self.body = body
        self.headers = headers


def check_success(response, success_codes):
    """
    Convert an HTTP response to an appropriate APIError if
    the response code does not match an expected success code.

    This is intended to be used as a callback for a deferred that fires with
    an IResponse provider.

    :param IResponse response: The response to check.
    :param list success_codes: A list of int HTTP response codes that indicate
        "success".

    :return: response or a deferred that errbacks with an APIError.
    """
    def _raise_api_error(body):
        raise APIError(response.code, body, response.headers)

    if response.code not in success_codes:
        return treq.content(response).addCallback(_raise_api_error)

    return response


def headers(auth_token=None):
    """
    Generate an appropriate set of headers given an auth_token.

    :param str auth_token: The auth_token or None.
    :return: A dict of common headers.
    """
    h = {'content-type': ['application/json'],
         'accept': ['application/json'],
         'User-Agent': ['OtterScale/0.0']}

    if auth_token is not None:
        h['x-auth-token'] = [auth_token]

    return h


def get_url_root():
    """
    Get the URL root
    :return: string containing the URL root
    """
    return config_value('url_root')


def get_collection_links(collection, url, rel, limit=None, marker=None):
    """
    Return links `dict` for given collection like below. The 'next' link is
    added only if number of items in `collection` has reached `limit`

        [
          {
            "href": <url with api version>,
            "rel": "self"
          },
          {
            "href": <url of next link>,
            "rel": "next"
          }
        ]

    :param collection: the collection whose links are required.
    :type collection: list of dict that has 'id' in it

    :param url: URL of the collection

    :param rel: What to put under 'rel'

    :param limit: pagination limit

    :param marker: pagination marker
    """
    limit = limit or config_value('limits.pagination') or 100
    links = []
    if not marker and rel is not None:
        links.append({'href': url, 'rel': rel})
    if len(collection) >= limit:
        query_params = {'limit': limit, 'marker': collection[limit - 1]['id']}
        next_url = "{0}?{1}".format(url, urlencode(query_params))
        links.append({'href': next_url, 'rel': 'next'})
    return links


def get_groups_links(groups, tenant_id, rel='self', limit=None, marker=None):
    """
    Get the links to groups along with 'next' link
    """
    url = get_autoscale_links(tenant_id, format=None)
    return get_collection_links(groups, url, rel, limit, marker)


def get_policies_links(policies, tenant_id, group_id, rel='self', limit=None, marker=None):
    """
    Get the links to groups along with 'next' link
    """
    url = get_autoscale_links(tenant_id, group_id, "", format=None)
    return get_collection_links(policies, url, rel, limit, marker)


def get_webhooks_links(webhooks, tenant_id, group_id, policy_id,
                       rel='self', limit=None, marker=None):
    """
    Get the links to webhooks along with 'next' link
    """
    url = get_autoscale_links(tenant_id, group_id, policy_id, "", format=None)
    return get_collection_links(webhooks, url, rel, limit, marker)


def get_autoscale_links(tenant_id, group_id=None, policy_id=None,
                        webhook_id=None, capability_hash=None,
                        capability_version="1", format="json",
                        api_version="1.0"):
    """
    Generates links into the autoscale system, based on the ids given.  If
    the format is "json", then a JSON blob will be given in the form of::

        [
          {
            "href": <url with api version>,
            "rel": "self"
          }
        ]

    Otherwise, the return value will just be the link.

    :param tenant_id: the tenant ID of the user
    :type tenant_id: ``str``

    :param group_id: the scaling group UUID - if not provided then the link(s)
        will be just the link to listing all scaling groups for the tenant
        ID/creating an autoscale group.
    :type group_id: ``str`` or ``None``

    :param policy_id: the scaling policy UUID - if not provided (and `group_id`
        is provided)then the link(s) will be just the link to the scaling group,
        and if blank then the link(s) will to listings of all the policies
        for the scaling group.
    :type policy_id: ``str`` or ``None``

    :param webhook_id: the webhook UUID - if not provided (and `group_id` and
        `policy_id` are provided) then the link(s) will be just the link to the
        scaling policy, and if blank then the link(s) will to listings of all
        the webhooks for the scaling policy
    :type webhook_id: ``str`` or ``None``

    :param format: whether to return a bunch of links in JSON format
    :type format: ``str`` that should be 'json' if the JSON format is desired

    :param api_version: Which API version to provide links to - generally
        should not be overriden
    :type api_version: ``str``

    :param capability_hash: a unique value for the capability url
    :type capability_hash: ``str``

    :param capability_version: capability hash generation version - defaults to
        1
    :type capability_version: ``str``

    :return: JSON blob if `format="json"` is given, a ``str`` containing a link
        else
    """
    api = "v{0}".format(api_version)
    segments = [get_url_root(), api, tenant_id, "groups"]

    if group_id is not None:
        segments.append(group_id)
        if policy_id is not None:
            segments.extend(("policies", policy_id))
            if webhook_id is not None:
                segments.extend(("webhooks", webhook_id))

    if segments[-1] != '':
        segments.append('')

    url = append_segments(*segments)

    if format == "json":
        links = [
            {"href": url, "rel": "self"}
        ]

        if capability_hash is not None:
            capability_url = append_segments(
                get_url_root(),
                api,
                "execute",
                capability_version,
                capability_hash, '')

            links.append({"href": capability_url, "rel": "capability"})

        return links
    else:
        return url


def transaction_id(request):
    """
    Extract the transaction id from the given request.

    :param IRequest request: The request we are trying to get the
        transaction id for.

    :returns: A string transaction id.
    """
    return request.responseHeaders.getRawHeaders('X-Response-Id')[0]
