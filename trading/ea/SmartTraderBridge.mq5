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
input int      InpMaxOpenOrders = 7;                       // Макс. одновременно активных позиций/ордеров
input bool     InpUseAutoRisk  = false;                    // Использовать расчет лота от баланса (%)
input double   InpRiskPercent  = 1.0;                      // Процент риска на сделку (%)
input double   InpFixedLot     = 0.01;                     // Фиксированный лот (если AutoRisk = false)
input int      InpSlippage     = 10;                       // Проскальзывание в пунктах

//--- Глобальные переменные
ulong    processed_signals[];

string   common_pairs[] = {
   "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "NZDUSD", "USDCAD",
   "EURGBP", "EURJPY", "GBPJPY", "EURAUD", "GBPAUD", "EURCHF", "CADJPY",
   "AUDCAD", "AUDNZD", "XAUUSD"
};

string GetBrokerSymbol(string standard_pair);

//+------------------------------------------------------------------+
//| Получение JSON текущих живых цен (тиков) для всех пар           |
//+------------------------------------------------------------------+
string GetLiveQuotesSummary()
{
   string json = "{";
   int count = 0;
   for(int i = 0; i < ArraySize(common_pairs); i++)
   {
      string pair = common_pairs[i];
      string sym = GetBrokerSymbol(pair);
      MqlTick tick;
      if(SymbolInfoTick(sym, tick))
      {
         if(count > 0) json += ",";
         json += StringFormat("\"%s\":{\"bid\":%.5f,\"ask\":%.5f}", pair, tick.bid, tick.ask);
         count++;
      }
   }
   json += "}";
   return json;
}

//+------------------------------------------------------------------+
//| Проверка и учет уже обработанных сигналов                       |
//+------------------------------------------------------------------+
bool IsSignalProcessed(int sig_id)
{
   if(sig_id <= 0) return false;
   for(int i = 0; i < ArraySize(processed_signals); i++)
   {
      if((int)processed_signals[i] == sig_id) return true;
   }
   return false;
}

void MarkSignalProcessed(int sig_id)
{
   if(sig_id <= 0) return;
   int sz = ArraySize(processed_signals);
   ArrayResize(processed_signals, sz + 1);
   processed_signals[sz] = (ulong)sig_id;
}

int export_timer_counter = 0;

void ExportBrokerRates();
string GetDealsHistorySummary();

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit()
{
   trade.SetExpertMagicNumber(InpMagicNumber);
   trade.SetDeviationInPoints(InpSlippage);
   trade.SetTypeFilling(ORDER_FILLING_IOC);
   
   ExportBrokerRates();
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

   export_timer_counter++;
   if(export_timer_counter >= 20) // каждые 60 секунд (20 * 3 сек)
   {
      export_timer_counter = 0;
      ExportBrokerRates();
   }
}

//+------------------------------------------------------------------+
//| Получение JSON открытых позиций                                  |
//+------------------------------------------------------------------+
string GetActivePositionsSummary()
{
   string json = "[";
   int count = 0;
   for(int i = 0; i < PositionsTotal(); i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0)
      {
         if(PositionGetInteger(POSITION_MAGIC) != InpMagicNumber) continue;
         if(count > 0) json += ",";
         string sym = PositionGetString(POSITION_SYMBOL);
         ENUM_POSITION_TYPE type = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
         double volume = PositionGetDouble(POSITION_VOLUME);
         double price = PositionGetDouble(POSITION_PRICE_OPEN);
         double sl = PositionGetDouble(POSITION_SL);
         double tp = PositionGetDouble(POSITION_TP);
         double profit = PositionGetDouble(POSITION_PROFIT);
         string type_str = (type == POSITION_TYPE_BUY) ? "BUY" : "SELL";
         json += StringFormat("{\"ticket\":%I64u,\"symbol\":\"%s\",\"type\":\"%s\",\"lot\":%.2f,\"price\":%.5f,\"sl\":%.5f,\"tp\":%.5f,\"profit\":%.2f}",
                              ticket, sym, type_str, volume, price, sl, tp, profit);
         count++;
      }
   }
   json += "]";
   return json;
}

//+------------------------------------------------------------------+
//| Получение JSON отложенных ордеров                                |
//+------------------------------------------------------------------+
string GetPendingOrdersSummary()
{
   string json = "[";
   int count = 0;
   for(int i = 0; i < OrdersTotal(); i++)
   {
      ulong ticket = OrderGetTicket(i);
      if(ticket > 0)
      {
         if(OrderGetInteger(ORDER_MAGIC) != InpMagicNumber) continue;
         if(count > 0) json += ",";
         string sym = OrderGetString(ORDER_SYMBOL);
         ENUM_ORDER_TYPE type = (ENUM_ORDER_TYPE)OrderGetInteger(ORDER_TYPE);
         double volume = OrderGetDouble(ORDER_VOLUME_CURRENT);
         double price = OrderGetDouble(ORDER_PRICE_OPEN);
         double sl = OrderGetDouble(ORDER_SL);
         double tp = OrderGetDouble(ORDER_TP);
         string type_str = "LIMIT";
         if(type == ORDER_TYPE_BUY_LIMIT) type_str = "BUY_LIMIT";
         else if(type == ORDER_TYPE_SELL_LIMIT) type_str = "SELL_LIMIT";
         else if(type == ORDER_TYPE_BUY_STOP) type_str = "BUY_STOP";
         else if(type == ORDER_TYPE_SELL_STOP) type_str = "SELL_STOP";
         
         json += StringFormat("{\"ticket\":%I64u,\"symbol\":\"%s\",\"type\":\"%s\",\"lot\":%.2f,\"price\":%.5f,\"sl\":%.5f,\"tp\":%.5f}",
                              ticket, sym, type_str, volume, price, sl, tp);
         count++;
      }
   }
   json += "]";
   return json;
}

//+------------------------------------------------------------------+
//| Экстренное закрытие всех позиций и ордеров (РЕЖИМ ПАНИКИ)        |
//+------------------------------------------------------------------+
void PanicCloseAll()
{
   Print("🚨 [SmartTraderBridge] Получена команда ПАНИКИ! Экстренное закрытие всех позиций и ордеров...");
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0)
      {
         trade.PositionClose(ticket);
      }
   }
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      ulong ticket = OrderGetTicket(i);
      if(ticket > 0)
      {
         trade.OrderDelete(ticket);
      }
   }
   Print("✅ [SmartTraderBridge] Режим паники исполнен: все позиции и ордера закрыты/сняты.");
}

//+------------------------------------------------------------------+
//| Получение JSON истории закрытых сделок брокера                   |
//+------------------------------------------------------------------+
string GetDealsHistorySummary()
{
   if(!HistorySelect(0, TimeCurrent())) return "[]";
   int total = HistoryDealsTotal();
   string json = "[";
   int count = 0;
   for(int i = 0; i < total; i++)
   {
      ulong ticket = HistoryDealGetTicket(i);
      if(ticket > 0)
      {
         if(HistoryDealGetInteger(ticket, DEAL_MAGIC) != InpMagicNumber) continue;
         ENUM_DEAL_ENTRY entry = (ENUM_DEAL_ENTRY)HistoryDealGetInteger(ticket, DEAL_ENTRY);
         if(entry == DEAL_ENTRY_OUT || entry == DEAL_ENTRY_INOUT)
         {
            double profit = HistoryDealGetDouble(ticket, DEAL_PROFIT);
            double price = HistoryDealGetDouble(ticket, DEAL_PRICE);
            double volume = HistoryDealGetDouble(ticket, DEAL_VOLUME);
            long time_close = HistoryDealGetInteger(ticket, DEAL_TIME);
            long magic = HistoryDealGetInteger(ticket, DEAL_MAGIC);
            string sym = HistoryDealGetString(ticket, DEAL_SYMBOL);
            string comment = HistoryDealGetString(ticket, DEAL_COMMENT);
            ENUM_DEAL_TYPE type = (ENUM_DEAL_TYPE)HistoryDealGetInteger(ticket, DEAL_TYPE);
            string type_str = (type == DEAL_TYPE_BUY) ? "BUY" : "SELL";
            
            StringReplace(comment, "\"", "'");
            StringReplace(comment, "\r", " ");
            StringReplace(comment, "\n", " ");

            if(count > 0) json += ",";
            json += StringFormat("{\"ticket\":%I64u,\"symbol\":\"%s\",\"type\":\"%s\",\"lot\":%.2f,\"price\":%.5f,\"profit\":%.2f,\"time\":%I64d,\"magic\":%I64d,\"comment\":\"%s\"}",
                                 ticket, sym, type_str, volume, price, profit, time_close, magic, comment);
            count++;
         }
      }
   }
   json += "]";
   return json;
}

//+------------------------------------------------------------------+
//| Экспорт баров брокера в CSV файлы в MQL5/Files                   |
//+------------------------------------------------------------------+
void ExportBrokerRates()
{
   ENUM_TIMEFRAMES tfs[4] = {PERIOD_M15, PERIOD_H1, PERIOD_H4, PERIOD_D1};
   string tf_names[4] = {"M15", "H1", "H4", "D1"};
   
   for(int p = 0; p < ArraySize(common_pairs); p++)
   {
      string standard_sym = common_pairs[p];
      string sym = GetBrokerSymbol(standard_sym);
      for(int t = 0; t < 4; t++)
      {
         MqlRates rates[];
         ArraySetAsSeries(rates, true);
         int copied = CopyRates(sym, tfs[t], 0, 100, rates);
         if(copied > 0)
         {
            string fname = "candles_" + standard_sym + "_" + tf_names[t] + ".csv";
            int h = FileOpen(fname, FILE_WRITE|FILE_CSV|FILE_ANSI, ",");
            if(h != INVALID_HANDLE)
            {
               FileWrite(h, "timestamp", "open", "high", "low", "close", "volume");
               for(int k = copied - 1; k >= 0; k--)
               {
                  FileWrite(h,
                     TimeToString(rates[k].time, TIME_DATE|TIME_MINUTES),
                     DoubleToString(rates[k].open, 5),
                     DoubleToString(rates[k].high, 5),
                     DoubleToString(rates[k].low, 5),
                     DoubleToString(rates[k].close, 5),
                     IntegerToString(rates[k].tick_volume)
                  );
               }
               FileClose(h);
            }
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Опрос сервера на наличие новых сигналов                          |
//+------------------------------------------------------------------+
void PollOrdersFromServer()
{
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double margin_free = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
   string broker = AccountInfoString(ACCOUNT_COMPANY);
   long account = AccountInfoInteger(ACCOUNT_LOGIN);

   string headers = "Content-Type: application/json\r\nUser-Agent: SmartTrader-MT5\r\nAccept: application/json\r\n";

   string pos_json = GetActivePositionsSummary();
   string ord_json = GetPendingOrdersSummary();
   string quotes_json = GetLiveQuotesSummary();
   string history_json = GetDealsHistorySummary();
   
   string url = InpServerUrl + "/api/v1/bridge/orders";
   
   string esc_broker = broker;
   StringReplace(esc_broker, "\"", "'");

   string body = StringFormat("{\"balance\":%.2f,\"equity\":%.2f,\"margin_free\":%.2f,\"broker\":\"%s\",\"account\":\"%I64d\",\"positions\":%s,\"orders\":%s,\"quotes\":%s,\"history\":%s}",
                              balance, equity, margin_free, esc_broker, account, pos_json, ord_json, quotes_json, history_json);

   char post_data[];
   char result_data[];
   string result_headers;
   StringToCharArray(body, post_data, 0, StringLen(body), CP_UTF8);
   
   int res = WebRequest("POST", url, headers, 3000, post_data, result_data, result_headers);
   if(res == 200)
   {
      string json = CharArrayToString(result_data);
      
      // Проверяем флаг экстренной паники
      if(StringFind(json, "\"panic_close_all\":true") >= 0 || StringFind(json, "\"panic_close_all\": true") >= 0)
      {
         PanicCloseAll();
      }
      
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

   for(int i = 0; i < ArraySize(common_pairs); i++)
   {
      string pair = common_pairs[i];
      int pos = StringFind(json, "\"" + pair + "\"");
      if(pos < 0) continue;

      // Получаем точное имя символа у брокера (с учетом суффиксов .pro, m, _i)
      string broker_symbol = GetBrokerSymbol(pair);

      // Извлекаем фрагмент вокруг пары (защита от отрицательного индекса в MQL5)
      int search_start = (int)MathMax(0, pos - 100);
      int block_start = StringFind(json, "{", search_start);
      int block_end   = StringFind(json, "}", pos);
      if(block_start < 0 || block_end < 0 || block_start > pos) continue;

      string block = StringSubstr(json, block_start, block_end - block_start + 1);

      // Проверяем направление
      bool is_long  = (StringFind(block, "\"LONG\"") >= 0);
      bool is_short = (StringFind(block, "\"SHORT\"") >= 0);
      if(!is_long && !is_short) continue;

      int sig_id = (int)ExtractDouble(block, "\"id\":");

      // Если сигнал уже обработан этим советником ранее (открыт или отклонен):
      if(sig_id > 0 && IsSignalProcessed(sig_id))
      {
         if(HasOpenPosition(broker_symbol))
         {
            if(StringFind(block, "\"breakeven_applied\":true") >= 0 || StringFind(block, "\"breakeven_applied\": true") >= 0)
            {
               ApplyBreakevenIfEligible(broker_symbol);
            }
         }
         continue;
      }

      // Проверяем, открыта ли уже позиция или отложенный ордер по этой паре с нашим Magic
      if(HasOpenPosition(broker_symbol) || HasPendingOrder(broker_symbol))
      {
         if(sig_id > 0) MarkSignalProcessed(sig_id);
         // Проверяем перенос в безубыток для открытых
         if(HasOpenPosition(broker_symbol))
         {
            if(StringFind(block, "\"breakeven_applied\":true") >= 0 || StringFind(block, "\"breakeven_applied\": true") >= 0)
            {
               ApplyBreakevenIfEligible(broker_symbol);
            }
         }
         continue;
      }

      // Проверяем глобальный лимит одновременно открытых позиций и отложенных ордеров
      if(CountTotalOpenAndPending() >= InpMaxOpenOrders)
      {
         Print("⚠️ [SmartTrader] Достигнут лимит открытых ордеров: ", InpMaxOpenOrders, ". Пропуск: ", broker_symbol);
         ReportExecution(pair, "REJECTED_LIMIT", 0.0, 0.0, StringFormat("Лимит слотов: %d/%d занято", InpMaxOpenOrders, InpMaxOpenOrders), sig_id);
         if(sig_id > 0) MarkSignalProcessed(sig_id);
         continue;
      }

      // Извлекаем SL, TP и Entry
      double entry = ExtractDouble(block, "\"entry\":");
      double sl    = ExtractDouble(block, "\"stop_loss\":");
      double tp    = ExtractDouble(block, "\"tp1\":");
      if(sl <= 0 || tp <= 0) continue;

      bool is_limit = (StringFind(block, "LIMIT") >= 0);

      // Рассчитываем лот
      double lot = InpFixedLot;
      if(InpUseAutoRisk)
      {
         lot = CalculateRiskLot(broker_symbol, sl);
      }

      // Открываем ордер с валидацией R:R
      if(is_long)
      {
         double ask = SymbolInfoDouble(broker_symbol, SYMBOL_ASK);
         if(is_limit && entry > 0 && entry < ask)
         {
            if(trade.BuyLimit(lot, entry, broker_symbol, sl, tp, ORDER_TIME_GTC, 0, "SmartTrader Limit"))
            {
               Print("✅ [SmartTrader] BUY_LIMIT выставлен: ", broker_symbol, " @ ", entry, " | SL: ", sl, " | TP: ", tp);
               ReportExecution(pair, "BUY_LIMIT", entry, 0.0, "Buy Limit выставлен", sig_id);
               if(sig_id > 0) MarkSignalProcessed(sig_id);
            }
            else
            {
               string err_msg = StringFormat("Ошибка BuyLimit #%d: %s", trade.ResultRetcode(), trade.ResultRetcodeDescription());
               Print("❌ [SmartTrader] ", err_msg);
               ReportExecution(pair, "REJECTED_ERROR", entry, 0.0, err_msg, sig_id);
               if(sig_id > 0) MarkSignalProcessed(sig_id);
            }
         }
         else
         {
            // Проверка реального R:R от рыночной цены перед входом по рынку
            double real_risk = MathAbs(ask - sl);
            double real_reward = MathAbs(tp - ask);
            double real_rr = (real_risk > 0) ? (real_reward / real_risk) : 0.0;
            if(real_rr < 1.8)
            {
               string rr_msg = StringFormat("Реальный R:R 1:%.2f < 1:1.80 (Защита капитала)", real_rr);
               Print("⛔ [SmartTrader] ОТМЕНА ВХОДА ПО РЫНКУ: ", broker_symbol, " (ASK=", ask, ", SL=", sl, ", TP=", tp, "). ", rr_msg);
               ReportExecution(pair, "REJECTED_RR", ask, 0.0, rr_msg, sig_id);
               if(sig_id > 0) MarkSignalProcessed(sig_id);
               continue;
            }

            // Spread check before execution
            double spread_points = SymbolInfoInteger(broker_symbol, SYMBOL_SPREAD);
            double point_val = SymbolInfoDouble(broker_symbol, SYMBOL_POINT);
            double spread_price = spread_points * point_val;
            double max_spread = real_risk * 0.15; // Spread should not exceed 15% of risk
            if(spread_price > max_spread && max_spread > 0)
            {
               Print("⚠️ [SmartTrader] Spread too high for ", broker_symbol, ": ", spread_price, " > max ", max_spread);
               continue;
            }

            if(trade.Buy(lot, broker_symbol, ask, sl, tp, "SmartTrader Institutional"))
            {
               Print("✅ [SmartTrader] BUY ордер открыт: ", broker_symbol, " | Лот: ", lot, " | SL: ", sl, " | TP: ", tp, " | R:R: 1:", DoubleToString(real_rr, 2));
               ReportExecution(pair, "BUY", ask, 0.0, StringFormat("Лот %.2f", lot), sig_id);
               if(sig_id > 0) MarkSignalProcessed(sig_id);
            }
            else
            {
               string err_msg = StringFormat("Ошибка Buy #%d: %s", trade.ResultRetcode(), trade.ResultRetcodeDescription());
               Print("❌ [SmartTrader] ", err_msg);
               ReportExecution(pair, "REJECTED_ERROR", ask, 0.0, err_msg, sig_id);
               if(sig_id > 0) MarkSignalProcessed(sig_id);
            }
         }
      }
      else if(is_short)
      {
         double bid = SymbolInfoDouble(broker_symbol, SYMBOL_BID);
         if(is_limit && entry > 0 && entry > bid)
         {
            if(trade.SellLimit(lot, entry, broker_symbol, sl, tp, ORDER_TIME_GTC, 0, "SmartTrader Limit"))
            {
               Print("✅ [SmartTrader] SELL_LIMIT выставлен: ", broker_symbol, " @ ", entry, " | SL: ", sl, " | TP: ", tp);
               ReportExecution(pair, "SELL_LIMIT", entry, 0.0, "Sell Limit выставлен", sig_id);
               if(sig_id > 0) MarkSignalProcessed(sig_id);
            }
            else
            {
               string err_msg = StringFormat("Ошибка SellLimit #%d: %s", trade.ResultRetcode(), trade.ResultRetcodeDescription());
               Print("❌ [SmartTrader] ", err_msg);
               ReportExecution(pair, "REJECTED_ERROR", entry, 0.0, err_msg, sig_id);
               if(sig_id > 0) MarkSignalProcessed(sig_id);
            }
         }
         else
         {
            // Проверка реального R:R от рыночной цены перед входом по рынку
            double real_risk = MathAbs(sl - bid);
            double real_reward = MathAbs(bid - tp);
            double real_rr = (real_risk > 0) ? (real_reward / real_risk) : 0.0;
            if(real_rr < 1.8)
            {
               string rr_msg = StringFormat("Реальный R:R 1:%.2f < 1:1.80 (Защита капитала)", real_rr);
               Print("⛔ [SmartTrader] ОТМЕНА ВХОДА ПО РЫНКУ: ", broker_symbol, " (BID=", bid, ", SL=", sl, ", TP=", tp, "). ", rr_msg);
               ReportExecution(pair, "REJECTED_RR", bid, 0.0, rr_msg, sig_id);
               if(sig_id > 0) MarkSignalProcessed(sig_id);
               continue;
            }

            // Spread check before execution
            double spread_points = SymbolInfoInteger(broker_symbol, SYMBOL_SPREAD);
            double point_val = SymbolInfoDouble(broker_symbol, SYMBOL_POINT);
            double spread_price = spread_points * point_val;
            double max_spread = real_risk * 0.15; // Spread should not exceed 15% of risk
            if(spread_price > max_spread && max_spread > 0)
            {
               Print("⚠️ [SmartTrader] Spread too high for ", broker_symbol, ": ", spread_price, " > max ", max_spread);
               continue;
            }

            if(trade.Sell(lot, broker_symbol, bid, sl, tp, "SmartTrader Institutional"))
            {
               Print("✅ [SmartTrader] SELL ордер открыт: ", broker_symbol, " | Лот: ", lot, " | SL: ", sl, " | TP: ", tp, " | R:R: 1:", DoubleToString(real_rr, 2));
               ReportExecution(pair, "SELL", bid, 0.0, StringFormat("Лот %.2f", lot), sig_id);
               if(sig_id > 0) MarkSignalProcessed(sig_id);
            }
            else
            {
               string err_msg = StringFormat("Ошибка Sell #%d: %s", trade.ResultRetcode(), trade.ResultRetcodeDescription());
               Print("❌ [SmartTrader] ", err_msg);
               ReportExecution(pair, "REJECTED_ERROR", bid, 0.0, err_msg, sig_id);
               if(sig_id > 0) MarkSignalProcessed(sig_id);
            }
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Получение символа брокера с учетом возможных суффиксов           |
//+------------------------------------------------------------------+
string GetBrokerSymbol(string standard_pair)
{
   if(SymbolInfoDouble(standard_pair, SYMBOL_BID) > 0)
      return standard_pair;
      
   int total = SymbolsTotal(false);
   for(int i = 0; i < total; i++)
   {
      string name = SymbolName(i, false);
      if(StringFind(name, standard_pair) >= 0)
      {
         SymbolSelect(name, true);
         return name;
      }
   }
   return standard_pair;
}

//+------------------------------------------------------------------+
//| Проверка наличия открытой позиции (регистронезависимо)          |
//+------------------------------------------------------------------+
bool HasOpenPosition(string symbol)
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(StringCompare(PositionGetSymbol(i), symbol, false) == 0)
      {
         if(PositionGetInteger(POSITION_MAGIC) == InpMagicNumber)
            return true;
      }
   }
   return false;
}

//+------------------------------------------------------------------+
//| Проверка наличия отложенного ордера (регистронезависимо)        |
//+------------------------------------------------------------------+
bool HasPendingOrder(string symbol)
{
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      ulong ticket = OrderGetTicket(i);
      if(ticket > 0 && StringCompare(OrderGetString(ORDER_SYMBOL), symbol, false) == 0)
      {
         if(OrderGetInteger(ORDER_MAGIC) == InpMagicNumber)
            return true;
      }
   }
   return false;
}

//+------------------------------------------------------------------+
//| Подсчет всех открытых позиций и отложенных ордеров советника    |
//+------------------------------------------------------------------+
int CountTotalOpenAndPending()
{
   int count = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(PositionGetInteger(POSITION_MAGIC) == InpMagicNumber)
         count++;
   }
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      ulong ticket = OrderGetTicket(i);
      if(ticket > 0 && OrderGetInteger(ORDER_MAGIC) == InpMagicNumber)
         count++;
   }
   return count;
}

//+------------------------------------------------------------------+
//| Перевод открытой позиции в безубыток                             |
//+------------------------------------------------------------------+
void ApplyBreakevenIfEligible(string symbol)
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(StringCompare(PositionGetSymbol(i), symbol, false) == 0 && PositionGetInteger(POSITION_MAGIC) == InpMagicNumber)
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
//| Отправка подробного отчета серверу                               |
//+------------------------------------------------------------------+
void ReportExecution(string symbol, string action, double price, double profit=0.0, string reason="", int signal_id=0)
{
   string url = InpServerUrl + "/api/v1/bridge/report";
   string headers = "Content-Type: application/json\r\n";
   string esc_reason = reason;
   StringReplace(esc_reason, "\"", "'");
   StringReplace(esc_reason, "\r", " ");
   StringReplace(esc_reason, "\n", " ");
   string body = StringFormat("{\"symbol\":\"%s\",\"action\":\"%s\",\"price\":%.5f,\"profit\":%.2f,\"reason\":\"%s\",\"signal_id\":%d}", 
                              symbol, action, price, profit, esc_reason, signal_id);
   char post_data[];
   char result_data[];
   string result_headers;
   StringToCharArray(body, post_data, 0, StringLen(body), CP_UTF8);
   WebRequest("POST", url, headers, 3000, post_data, result_data, result_headers);
}


//+------------------------------------------------------------------+
//| Trade transaction handler: мгновенное оповещение о закрытии      |
//+------------------------------------------------------------------+
void OnTradeTransaction(const MqlTradeTransaction& trans, const MqlTradeRequest& request, const MqlTradeResult& result)
{
   if(trans.type == TRADE_TRANSACTION_DEAL_ADD)
   {
      ulong deal_ticket = trans.deal;
      HistorySelect(0, TimeCurrent());
      if(deal_ticket > 0 && HistoryDealSelect(deal_ticket))
      {
         long magic = HistoryDealGetInteger(deal_ticket, DEAL_MAGIC);
         if(magic == InpMagicNumber)
         {
            ENUM_DEAL_ENTRY entry_type = (ENUM_DEAL_ENTRY)HistoryDealGetInteger(deal_ticket, DEAL_ENTRY);
            if(entry_type == DEAL_ENTRY_OUT || entry_type == DEAL_ENTRY_INOUT)
            {
               double profit = HistoryDealGetDouble(deal_ticket, DEAL_PROFIT);
               double close_price = HistoryDealGetDouble(deal_ticket, DEAL_PRICE);
               string sym = HistoryDealGetString(deal_ticket, DEAL_SYMBOL);
               string comment = HistoryDealGetString(deal_ticket, DEAL_COMMENT);
               Print("📢 [SmartTrader Bridge] Закрыта сделка ", sym, " #", deal_ticket, " | Профит: ", profit, " USD | Цена: ", close_price);
               ReportExecution(sym, "DEAL_CLOSED", close_price, profit, comment, 0);
            }
         }
      }
   }
}


