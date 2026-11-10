"""Local Flask launch; production uses the checked-in Gunicorn configuration."""
import argparse
from dotenv import load_dotenv

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--host',default='127.0.0.1')
    parser.add_argument('--port',type=int,default=8000)
    parser.add_argument('--env-file',default='.env')
    options=parser.parse_args()
    load_dotenv(options.env_file)
    from app.main import app
    app.start_services()
    try:app.run(host=options.host,port=options.port,threaded=True,use_reloader=False)
    finally:app.stop_services()

if __name__=='__main__':main()
