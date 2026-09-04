//+------------------------------------------------------------------+
//|                                           SmartTraderBridge.mq5  |
//|                        Smart Trader Bot — Institutional Bridge   |
//|                                      https://t.me/Smarter_tradebot|
//+------------------------------------------------------------------+
#property copyright "Smart Trader Bot"
#property link      "https://t.me/Smarter_tradebot"
#property version   "1.00"
#property strict

#include <Trade\Trade.mqh>
CTrade trade;

//--- Входные параметры советника
input group "=== НАСТРОЙКИ СЕРВЕРА ==="
input string   InpServerUrl    = "http://127.0.0.1:8080"; // URL сервера Smart Trader Bot
input int      InpPollInterval = 3;                       // Опрос сервера каждые N секунд
input ulong    InpMagicNumber  = 888001;                   // Magic Number ордеров

input group "=== УПРАВЛЕНИЕ РИСКОМ ==="
input bool     InpUseAutoRisk  = false;                    // Использовать расчет лота от баланса (%)
input double   InpRiskPercent  = 1.0;                      // Процент риска на сделку (%)
input double   InpFixedLot     = 0.01;                     // Фиксированный лот (если AutoRisk = false)
input int      InpSlippage     = 10;                       // Проскальзывание в пунктах

//--- Глобальные переменные
datetime last_poll_time = 0;
ulong    processed_signals[];

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   trade.SetExpertMagicNumber(InpMagicNumber);
   trade.SetDeviationInPoints(InpSlippage);
   trade.SetTypeFilling(ORDER_FILLING_IOC);
   
   EventSetTimer(InpPollInterval);
   Print("🏛 [SmartTraderBridge MT5] Советник запущен. URL: ", InpServerUrl);
   Print("ℹ️ Убедитесь, что URL ", InpServerUrl, " добавлен в Сервис -> Настройки -> Советники -> Разрешить WebRequest");
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();
   Print("🏛 [SmartTraderBridge MT5] Советник остановлен.");
}

//+------------------------------------------------------------------+
//| Expert timer function                                            |
//+------------------------------------------------------------------+
void OnTimer()
{
   PollOrdersFromServer();
}

//+------------------------------------------------------------------+
//| Опрос сервера на наличие новых сигналов                          |
//+------------------------------------------------------------------+
void PollOrdersFromServer()
{
   string url = InpServerUrl + "/api/v1/bridge/orders";
   string headers = "User-Agent: SmartTrader-MT5\r\nAccept: application/json\r\n";
   char post_data[];
   char result_data[];
   string result_headers;
   
   int res = WebRequest("GET", url, headers, 3000, post_data, result_data, result_headers);
   if(res == 200)
   {
      string json = CharArrayToString(result_data);
      ParseAndExecuteOrders(json);
   }
   else if(res == -1)
   {
      Print("⚠️ [SmartTraderBridge] Ошибка WebRequest. Код: ", GetLastError(), 
            ". Добавьте ", InpServerUrl, " в список разрешенных URL в MT5!");
   }
}

//+------------------------------------------------------------------+
//| Простейший парсинг и исполнение сигналов                         |
//+------------------------------------------------------------------+
void ParseAndExecuteOrders(string json)
{
   // Проверяем статус автопилота в ответе
   if(StringFind(json, "\"autotrade_enabled\":false") >= 0 || StringFind(json, "\"autotrade_enabled\": false") >= 0)
   {
      return; // Автопилот выключен
   }

   // Ищем символы в JSON (EURUSD, GBPUSD, USDJPY, XAUUSD и т.д.)
   string common_pairs[] = {
      "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "NZDUSD", "USDCAD",
      "EURGBP", "EURJPY", "GBPJPY", "EURAUD", "GBPAUD", "EURCHF", "CADJPY", "XAUUSD"
   };

   for(int i = 0; i < ArraySize(common_pairs); i++)
   {
      string pair = common_pairs[i];
      int pos = StringFind(json, "\"" + pair + "\"");
      if(pos < 0) continue;

      // Извлекаем фрагмент вокруг пары
      int block_start = StringFind(json, "{", pos - 50);
      int block_end   = StringFind(json, "}", pos);
      if(block_start < 0 || block_end < 0) continue;

      string block = StringSubstr(json, block_start, block_end - block_start + 1);

      // Проверяем направление
      bool is_long  = (StringFind(block, "\"LONG\"") >= 0);
      bool is_short = (StringFind(block, "\"SHORT\"") >= 0);
      if(!is_long && !is_short) continue;

      // Проверяем, открыта ли уже позиция по этой паре с нашим Magic
      if(HasOpenPosition(pair))
      {
         // Проверяем перенос в безубыток
         if(StringFind(block, "\"breakeven_applied\":true") >= 0 || StringFind(block, "\"breakeven_applied\": true") >= 0)
         {
            ApplyBreakevenIfEligible(pair);
         }
         continue;
      }

      // Извлекаем SL и TP
      double sl = ExtractDouble(block, "\"stop_loss\":");
      double tp = ExtractDouble(block, "\"tp1\":");
      if(sl <= 0 || tp <= 0) continue;

      // Рассчитываем лот
      double lot = InpFixedLot;
      if(InpUseAutoRisk)
      {
         lot = CalculateRiskLot(pair, sl);
      }

      // Открываем ордер
      if(is_long)
      {
         double ask = SymbolInfoDouble(pair, SYMBOL_ASK);
         if(trade.Buy(lot, pair, ask, sl, tp, "SmartTrader Institutional"))
         {
            Print("✅ [SmartTrader] BUY ордер открыт: ", pair, " | Лот: ", lot, " | SL: ", sl, " | TP: ", tp);
            ReportExecution(pair, "BUY", ask);
         }
      }
      else if(is_short)
      {
         double bid = SymbolInfoDouble(pair, SYMBOL_BID);
         if(trade.Sell(lot, pair, bid, sl, tp, "SmartTrader Institutional"))
         {
            Print("✅ [SmartTrader] SELL ордер открыт: ", pair, " | Лот: ", lot, " | SL: ", sl, " | TP: ", tp);
            ReportExecution(pair, "SELL", bid);
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Проверка наличия открытой позиции                               |
//+------------------------------------------------------------------+
bool HasOpenPosition(string symbol)
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(PositionGetSymbol(i) == symbol)
      {
         if(PositionGetInteger(POSITION_MAGIC) == InpMagicNumber)
            return true;
      }
   }
   return false;
}

//+------------------------------------------------------------------+
//| Перевод открытой позиции в безубыток                             |
//+------------------------------------------------------------------+
void ApplyBreakevenIfEligible(string symbol)
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(PositionGetSymbol(i) == symbol && PositionGetInteger(POSITION_MAGIC) == InpMagicNumber)
      {
         ulong ticket = PositionGetTicket(i);
         double open_price = PositionGetDouble(POSITION_PRICE_OPEN);
         double curr_sl    = PositionGetDouble(POSITION_SL);
         double tp         = PositionGetDouble(POSITION_TP);
         
         // Если SL еще не на точке входа
         if(MathAbs(curr_sl - open_price) > Point())
         {
            trade.PositionModify(ticket, open_price, tp);
            Print("🛡 [SmartTrader] Позиция ", symbol, " переведена в БЕЗУБЫТОК (SL = Entry: ", open_price, ")");
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Расчет объема лота на основе % риска от баланса                 |
//+------------------------------------------------------------------+
double CalculateRiskLot(string symbol, double sl_price)
{
   double balance   = AccountInfoDouble(ACCOUNT_BALANCE);
   double risk_amt  = balance * (InpRiskPercent / 100.0);
   double ask       = SymbolInfoDouble(symbol, SYMBOL_ASK);
   double diff      = MathAbs(ask - sl_price);
   if(diff <= 0) return InpFixedLot;
   
   double tick_size = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE);
   double tick_val  = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE);
   if(tick_size <= 0 || tick_val <= 0) return InpFixedLot;
   
   double pips = diff / tick_size;
   double lot = risk_amt / (pips * tick_val);
   
   double min_lot = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
   double max_lot = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX);
   double step    = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
   
   lot = MathFloor(lot / step) * step;
   return MathMax(min_lot, MathMin(max_lot, lot));
}

//+------------------------------------------------------------------+
//| Вспомогательное извлечение double из JSON                        |
//+------------------------------------------------------------------+
double ExtractDouble(string text, string key)
{
   int p = StringFind(text, key);
   if(p < 0) return 0.0;
   int start = p + StringLen(key);
   while(start < StringLen(text) && (StringGetCharacter(text, start) == ' ' || StringGetCharacter(text, start) == ':'))
      start++;
   int end = start;
   while(end < StringLen(text) && ((StringGetCharacter(text, end) >= '0' && StringGetCharacter(text, end) <= '9') || StringGetCharacter(text, end) == '.'))
      end++;
   string val_str = StringSubstr(text, start, end - start);
   return StringToDouble(val_str);
}

//+------------------------------------------------------------------+
//| Отправка отчета серверу                                          |
//+------------------------------------------------------------------+
void ReportExecution(string symbol, string action, double price)
{
   string url = InpServerUrl + "/api/v1/bridge/report";
   string headers = "Content-Type: application/json\r\n";
   string body = StringFormat("{\"symbol\":\"%s\",\"action\":\"%s\",\"price\":%f}", symbol, action, price);
   char post_data[];
   char result_data[];
   string result_headers;
   StringToCharArray(body, post_data, 0, WHOLE_ARRAY, CP_UTF8);
   WebRequest("POST", url, headers, 3000, post_data, result_data, result_headers);
}
