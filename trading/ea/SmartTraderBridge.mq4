//+------------------------------------------------------------------+
//|                                           SmartTraderBridge.mq4  |
//|                        Smart Trader Bot — Institutional Bridge   |
//|                                      https://t.me/Smarter_tradebot|
//+------------------------------------------------------------------+
#property copyright "Smart Trader Bot"
#property link      "https://t.me/Smarter_tradebot"
#property version   "1.00"
#property strict

//--- Входные параметры советника
input string   InpServerUrl    = "http://127.0.0.1:8080"; // URL сервера Smart Trader Bot
input int      InpPollInterval = 3;                       // Опрос каждые N секунд
input int      InpMagicNumber  = 888001;                   // Magic Number
input double   InpFixedLot     = 0.01;                     // Торговый лот
input int      InpSlippage     = 10;                       // Проскальзывание

int OnInit()
{
   EventSetTimer(InpPollInterval);
   Print("🏛 [SmartTraderBridge MT4] Советник запущен. URL: ", InpServerUrl);
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   Print("🏛 [SmartTraderBridge MT4] Советник остановлен.");
}

void OnTimer()
{
   PollOrdersFromServer();
}

void PollOrdersFromServer()
{
   string url = InpServerUrl + "/api/v1/bridge/orders";
   string headers = "User-Agent: SmartTrader-MT4\r\nAccept: application/json\r\n";
   char post_data[];
   char result_data[];
   string result_headers;
   
   int res = WebRequest("GET", url, headers, 3000, post_data, result_data, result_headers);
   if(res == 200)
   {
      string json = CharArrayToString(result_data);
      ParseAndExecute(json);
   }
}

void ParseAndExecute(string json)
{
   if(StringFind(json, "\"autotrade_enabled\":false") >= 0 || StringFind(json, "\"autotrade_enabled\": false") >= 0)
      return;

   string pairs[] = {"EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "NZDUSD", "USDCAD", "EURGBP", "EURJPY", "GBPJPY", "EURAUD", "XAUUSD"};
   for(int i = 0; i < ArraySize(pairs); i++)
   {
      string pair = pairs[i];
      if(StringFind(json, "\"" + pair + "\"") < 0) continue;

      if(HasOpenOrder(pair)) continue;

      int p = StringFind(json, "\"" + pair + "\"");
      int start_b = StringFind(json, "{", p - 50);
      int end_b   = StringFind(json, "}", p);
      if(start_b < 0 || end_b < 0) continue;
      string block = StringSubstr(json, start_b, end_b - start_b + 1);

      bool is_buy = (StringFind(block, "\"LONG\"") >= 0);
      bool is_sell = (StringFind(block, "\"SHORT\"") >= 0);
      if(!is_buy && !is_sell) continue;

      double sl = ExtractVal(block, "\"stop_loss\":");
      double tp = ExtractVal(block, "\"tp1\":");
      if(sl <= 0 || tp <= 0) continue;

      int ticket = -1;
      if(is_buy)
      {
         double ask = MarketInfo(pair, MODE_ASK);
         ticket = OrderSend(pair, OP_BUY, InpFixedLot, ask, InpSlippage, sl, tp, "SmartTrader", InpMagicNumber, 0, clrGreen);
      }
      else if(is_sell)
      {
         double bid = MarketInfo(pair, MODE_BID);
         ticket = OrderSend(pair, OP_SELL, InpFixedLot, bid, InpSlippage, sl, tp, "SmartTrader", InpMagicNumber, 0, clrRed);
      }

      if(ticket > 0)
      {
         Print("✅ [SmartTrader MT4] Ордер успешно открыт: ", pair, " Ticket: ", ticket);
      }
   }
}

bool HasOpenOrder(string symbol)
{
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      if(OrderSelect(i, SELECT_BY_POS, MODE_TRADES))
      {
         if(OrderSymbol() == symbol && OrderMagicNumber() == InpMagicNumber)
            return true;
      }
   }
   return false;
}

double ExtractVal(string text, string key)
{
   int p = StringFind(text, key);
   if(p < 0) return 0.0;
   int start = p + StringLen(key);
   while(start < StringLen(text) && (StringGetCharacter(text, start) == ' ' || StringGetCharacter(text, start) == ':'))
      start++;
   int end = start;
   while(end < StringLen(text) && ((StringGetCharacter(text, end) >= '0' && StringGetCharacter(text, end) <= '9') || StringGetCharacter(text, end) == '.'))
      end++;
   return StringToDouble(StringSubstr(text, start, end - start));
}
