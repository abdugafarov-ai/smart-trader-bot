import yfinance as yf

for sym in ['GC=F', 'XAUUSD=X']:
    try:
        t = yf.Ticker(sym)
        hist = t.history(period='5d', interval='1h')
        if not hist.empty:
            print(f"{sym}: OK, len={len(hist)}, last_close={hist['Close'].iloc[-1]:.2f}")
        else:
            print(f"{sym}: EMPTY")
    except Exception as e:
        print(f"{sym}: ERROR {e}")
