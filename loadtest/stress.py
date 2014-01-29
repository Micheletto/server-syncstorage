# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
"""
Load test for the SyncStorage server
"""

import os
import base64
import random
import json
import time
from urlparse import urlparse, urlunparse

import tokenlib
import hawkauthlib
import browserid.jwt
import browserid.tests.support
import requests.auth

from loads import TestCase


MOCKMYID_PRIVATE_KEY = browserid.jwt.DS128Key({
    "algorithm": "DS",
    "x": "385cb3509f086e110c5e24bdd395a84b335a09ae",
    "y": "738ec929b559b604a232a9b55a5295afc368063bb9c20fac4e53a74970a4db795"
         "6d48e4c7ed523405f629b4cc83062f13029c4d615bbacb8b97f5e56f0c7ac9bc1"
         "d4e23809889fa061425c984061fca1826040c399715ce7ed385c4dd0d40225691"
         "2451e03452d3c961614eb458f188e3e8d2782916c43dbe2e571251ce38262",
    "p": "ff600483db6abfc5b45eab78594b3533d550d9f1bf2a992a7a8daa6dc34f8045a"
         "d4e6e0c429d334eeeaaefd7e23d4810be00e4cc1492cba325ba81ff2d5a5b305a"
         "8d17eb3bf4a06a349d392e00d329744a5179380344e82a18c47933438f891e22a"
         "eef812d69c8f75e326cb70ea000c3f776dfdbd604638c2ef717fc26d02e17",
    "q": "e21e04f911d1ed7991008ecaab3bf775984309c3",
    "g": "c52a4a0ff3b7e61fdf1867ce84138369a6154f4afa92966e3c827e25cfa6cf508b"
         "90e5de419e1337e07a2e9e2a3cd5dea704d175f8ebf6af397d69e110b96afb17c7"
         "a03259329e4829b0d03bbc7896b15b4ade53e130858cc34d96269aa89041f40913"
         "6c7242a38895c9d5bccad4f389af1d7a4bd1398bd072dffa896233397a",
})


# The collections to operate on.
# Each operation will randomly select a collection from this list.
# The "tabs" collection is not included since it uses memcache; we need
# to figure out a way to test it without overloading the server.
collections = ['bookmarks', 'forms', 'passwords', 'history', 'prefs']

# The distribution of GET operations to meta/global per test run.
# 40% will do 0 GETs, 60% will do 1 GET, etc...
metaglobal_count_distribution = [40, 60, 0, 0, 0]

# The distribution of GET operations per test run.
# 71% will do 0 GETs, 15% will do 1 GET, etc...
get_count_distribution = [71, 15, 7, 4, 3]

# The distribution of POST operations per test run.
# 67% will do 0 POSTs, 18% will do 1 POST, etc...
post_count_distribution = [67, 18, 9, 4, 2]

# The distribution of DELETE operations per test run.
# 99% will do 0 DELETEs, 1% will do 1 DELETE, etc...
delete_count_distribution = [99, 1, 0, 0, 0]

# The probability that we'll try to do a full DELETE of all data.
# Expressed as a float between 0 and 1.
deleteall_probability = 1 / 100.


class HawkAuth(requests.auth.AuthBase):

    def __init__(self, server_url, id, secret):
        self.server_url = server_url
        self.id = id
        self.secret = secret

    def __call__(self, req):
        # Requets doesn't seem to include the port in the Host header,
        # and loads replaces hostnames with IPs.  Undo all this rubbish
        # so that we can calculate the correct signature.
        req.headers['Host'] = urlparse(self.server_url).netloc
        hawkauthlib.sign_request(req, self.id, self.secret)
        return req


class StressTest(TestCase):

    server_url = "https://token.dev.lcip.org"

    def test_storage_session(self):
        self._generate_token_credentials()
        auth = HawkAuth(self.endpoint_url, self.auth_token, self.auth_secret)

        headers = {"content-type": "application/json"}

        # Always GET info/collections
        url = self.endpoint_url + "/info/collections"
        response = self.session.get(url, auth=auth)
        self.assertTrue(response.status_code in (200, 404))

        # GET requests to meta/global.
        num_requests = self._pick_weighted_count(metaglobal_count_distribution)
        for x in range(num_requests):
            url = self.endpoint_url + "/storage/meta/global"
            response = self.session.get(url, auth=auth)
            if response.status_code == 404:
                metapayload = "This is the metaglobal payload which contains"\
                              " some client data that doesnt look much"\
                              " like this"
                data = json.dumps({"id": "global", "payload": metapayload})
                response = self.session.put(url, data=data, headers=headers, auth=auth)
            self.assertEqual(response.status_code, 200)

        # GET requests to individual collections.
        num_requests = self._pick_weighted_count(get_count_distribution)
        cols = random.sample(collections, num_requests)
        for x in range(num_requests):
            url = self.endpoint_url + "/storage/" + cols[x]
            newer = int(time.time() - random.randint(3600, 360000))
            params = {"full": "1", "newer": str(newer)}
            response = self.session.get(url, params=params, auth=auth)
            self.assertTrue(response.status_code in (200, 404))

        # PUT requests with 100 WBOs batched together
        num_requests = self._pick_weighted_count(post_count_distribution)
        cols = random.sample(collections, num_requests)
        for x in range(num_requests):
            url = self.endpoint_url + "/storage/" + cols[x]
            data = []
            items_per_batch = 10
            for i in range(items_per_batch):
                id = base64.urlsafe_b64encode(os.urandom(10)).rstrip("=")
                id += str(int((time.time() % 100) * 100000))
                payload = self.auth_token * random.randint(50, 200)
                wbo = {'id': id, 'payload': payload}
                data.append(wbo)
            data = json.dumps(data)
            response = self.session.post(url, data=data, headers=headers, auth=auth)
            self.assertEqual(response.status_code, 200)
            body = response.content
            self.assertTrue(body != '')
            result = json.loads(body)
            self.assertEquals(len(result["success"]), items_per_batch)
            self.assertEquals(len(result["failed"]), 0)

        # DELETE requests.
        # We might choose to delete some individual collections, or to do
        # a full reset and delete all the data.  Never both in the same run.
        num_requests = self._pick_weighted_count(delete_count_distribution)
        if num_requests:
            cols = random.sample(collections, num_requests)
            for x in range(num_requests):
                url = self.endpoint_url + "/storage/" + cols[x]
                response = self.session.delete(url, headers={"X-Confirm-Delete": "1"}, auth=auth)
                self.assertTrue(response.status_code in (200, 204))
        else:
            if random.random() <= deleteall_probability:
                url = self.endpoint_url + "/storage"
                response = self.session.delete(url, headers={"X-Confirm-Delete": "1"}, auth=auth)
                self.assertEquals(response.status_code, 200)

    def _generate_token_credentials(self):
        """Pick an identity, log in and generate the auth token."""
        # If the server_url has a hash fragment, it's a storage node and
        # that's the secret.  Otherwise it's a token server url.
        uid = random.randint(1, 1000000)
        url = urlparse(self.server_url)
        if url.fragment:
            endpoint = url._replace(fragment="", path="/1.5/" + str(uid))
            self.endpoint_url = urlunparse(endpoint)
            data = {"uid": uid, "node": urlunparse(url._replace(fragment=""))}
            self.auth_token = tokenlib.make_token(data, secret=url.fragment)
            self.auth_secret = tokenlib.get_derived_secret(self.auth_token,
                                                           secret=url.fragment)
        else:
            email = "user%s@mockmyid.com" % (uid,)
            assertion = browserid.tests.support.make_assertion(
                email=email,
                audience=self.server_url,
                issuer="mockmyid.com",
                issuer_keypair=(None, MOCKMYID_PRIVATE_KEY),
            )
            token_url = self.server_url + "/1.0/sync/1.5"
            response = self.session.get(token_url, headers={
                "Authorization": "BrowserID " + assertion,
            })
            response.raise_for_status()
            credentials = response.json()
            self.auth_token = credentials["id"].encode('ascii')
            self.auth_secret = credentials["key"].encode('ascii')
            self.endpoint_url = credentials["api_endpoint"]
        return self.auth_token, self.auth_secret, self.endpoint_url

    def _pick_weighted_count(self, weights):
        i = random.randint(1, sum(weights))
        count = 0
        base = 0
        for weight in weights:
            base += weight
            if i <= base:
                break
            count += 1
        return count
