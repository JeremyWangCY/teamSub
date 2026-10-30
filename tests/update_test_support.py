"""Isolate GUI tests from user settings, meetings and external network calls."""
import atexit
import os
from pathlib import Path
import sys
import tempfile
import urllib.parse
import urllib.request

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
temporary=tempfile.TemporaryDirectory(prefix='teamsub-update-tests-')
os.environ['TEAMSUB_DATA_DIR']=temporary.name
import translator as T
import teamsub as A
T.Config(enabled=False).save()
A.CaptionReader.start=lambda self:None

original_urlopen=urllib.request.urlopen
def local_only(request,*args,**kwargs):
    url=request.full_url if hasattr(request,'full_url') else request
    if urllib.parse.urlparse(url).hostname not in ('127.0.0.1','localhost','::1'):
        raise AssertionError('External requests are disabled in GUI tests')
    return original_urlopen(request,*args,**kwargs)
urllib.request.urlopen=local_only
atexit.register(temporary.cleanup)
