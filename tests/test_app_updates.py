import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app_updates as U


def payload(tag='v0.1.12', **extra):
    data=dict(tag_name=tag,html_url=f'{U.RELEASES_URL}/tag/{tag}',draft=False,prerelease=False)
    data.update(extra)
    return io.BytesIO(json.dumps(data).encode('utf-8'))


class UpdateTests(unittest.TestCase):
    def test_numeric_comparison(self):
        self.assertGreater(U.version_tuple('v0.1.10'),U.version_tuple('0.1.9'))
        self.assertTrue(U.Release('0.2.0','').newer_than('0.1.11'))
        self.assertFalse(U.Release('0.1.11','').newer_than('0.1.11'))
        self.assertFalse(U.Release('0.1.9','').newer_than('0.1.11'))

    def test_public_metadata_only(self):
        with patch.object(U.urllib.request,'urlopen',return_value=payload()) as fetch:
            release=U.check_latest_release()
        request=fetch.call_args.args[0]
        self.assertEqual(request.full_url,U.LATEST_API_URL)
        self.assertIsNone(request.data)
        self.assertEqual(request.get_method(),'GET')
        self.assertEqual({k.lower() for k,v in request.header_items()},
                         {'accept','user-agent','x-github-api-version'})
        self.assertEqual(fetch.call_args.kwargs['timeout'],10)
        self.assertEqual(release.version,'0.1.12')

    def test_untrusted_release_data(self):
        for bad in (payload(prerelease=True),payload(draft=True),payload('v0.2.0-rc.1'),
                    payload(html_url='https://example.com/download.exe'),
                    io.BytesIO(b'[]'),io.BytesIO(b'not json'),
                    io.BytesIO(b'x'*(U.MAX_RESPONSE_BYTES+1))):
            with self.subTest(response=bad),patch.object(U.urllib.request,'urlopen',return_value=bad):
                with self.assertRaises(U.UpdateCheckError):U.check_latest_release()

    def test_network_failures_are_not_latest(self):
        for error in (urllib.error.URLError('offline'),TimeoutError(),
                      urllib.error.HTTPError(U.LATEST_API_URL,404,'not found',None,None),
                      urllib.error.HTTPError(U.LATEST_API_URL,403,'rate limit',None,None)):
            with self.subTest(error=type(error).__name__),patch.object(U.urllib.request,'urlopen',side_effect=error):
                with self.assertRaises(U.UpdateCheckError):U.check_latest_release()


if __name__=='__main__':unittest.main(verbosity=2)
