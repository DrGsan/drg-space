import os, secrets, tempfile, signal, subprocess, sys
os.environ['PYTHON_DOTENV_DISABLED']='1'
os.environ['DATABASE_URL']=os.getenv('DRG_TEST_DATABASE_URL','')
os.environ['DATABASE_SCHEMA']=os.getenv('DRG_BROWSER_DATABASE_SCHEMA','drg_browser_'+secrets.token_hex(8))
os.environ['DATA_ENCRYPTION_ENABLED']=os.getenv('DRG_TEST_ENCRYPTION_ENABLED','true')
os.environ['DATA_ENCRYPTION_KEY']='dGVzdC1vbmx5LWVuY3J5cHRpb24ta2V5LTMyLWJ5dGU='
os.environ['APP_SECRET']=secrets.token_urlsafe(48)
os.environ['SETUP_TOKEN']='browser-test-setup-key'
port=int(os.getenv('DRG_BROWSER_PORT','8001'))
os.environ['PUBLIC_URL']='http://127.0.0.1:'+str(port)
# Exercise the production Flask/Gunicorn transport, including pooled HTTP and WS.
with tempfile.TemporaryDirectory(prefix='drg-browser-') as directory:
    os.environ['DATA_DIR']=directory
    server=subprocess.Popen([sys.executable,'-m','gunicorn','--no-control-socket','--config','gunicorn.conf.py','--bind','127.0.0.1:'+str(port),'tests.browser_app:app'])
    def stop(signum, frame):
        if server.poll() is None:server.send_signal(signum)
    signal.signal(signal.SIGTERM,stop)
    signal.signal(signal.SIGINT,stop)
    try:
        code=server.wait()
    finally:
        if server.poll() is None:
            server.terminate()
            try:server.wait(timeout=70)
            except subprocess.TimeoutExpired:server.kill();server.wait()
    sys.exit(code)
