import uvicorn
from purr.config import CFG

if __name__ == "__main__":
    print(f"purr → http://127.0.0.1:{CFG['port']}" + (f"/?key={CFG['access_key']}" if CFG["access_key"] else ""))
    uvicorn.run("purr.server:app", host=CFG["host"], port=CFG["port"], log_level="warning")
