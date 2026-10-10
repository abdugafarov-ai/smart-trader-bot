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
input string   InpApiKey       = "stb-default-key-change-me-2026"; // API Key for Bridge
input int      InpPollInterval = 3;                       // Опрос сервера каждые N секунд
input ulong    InpMagicNumber  = 888001;                   // Magic Number ордеров

input group "=== УПРАВЛЕНИЕ РИСКОМ ==="
input int      InpMaxOpenOrders = 3;                       // Макс. одновременно активных позиций/ордеров
input bool     InpUseAutoRisk  = false;                    // Использовать расчет лота от баланса (%)
input double   InpRiskPercent  = 1.0;                      // Процент риска на сделку (%)
input double   InpFixedLot     = 0.01;                     // Фиксированный лот (если AutoRisk = false)
input int      InpSlippage     = 20;                       // Проскальзывание в пунктах
input bool     InpUseAutoBE    = true;                     // Использовать авто-безубыток (+1.0R защищен)

//--- Глобальные переменные
ulong    processed_signals[];

string   common_pairs[] = {
   "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "NZDUSD", "USDCAD",
   "EURGBP", "EURJPY", "GBPJPY", "EURAUD", "GBPAUD", "EURCHF", "CADJPY",
   "AUDCAD", "AUDNZD"
};

string GetBrokerSymbol(string standard_pair);
void   ReportExecution(string symbol, string action, double price, double profit=0.0, string reason="", int signal_id=0, ulong ticket=0);
void   SetOptimalFillingMode(string symbol);
void   CleanupStalePendingOrders();
double CalculateRiskLot(string symbol, double entry_price, double sl_price, double custom_risk = 0.0, double fallback_lot = 0.01);

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
   // Ограничиваем массив 200 записями (FIFO) для предотвращения утечки памяти
   if(sz >= 200)
   {
      // Сдвигаем массив: удаляем первые 50 элементов
      for(int i = 0; i < sz - 50; i++)
         processed_signals[i] = processed_signals[i + 50];
      sz = sz - 50;
      ArrayResize(processed_signals, sz);
   }
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
   ManageOpenPositions(); // Институциональный мониторинг и авто-безубыток открытых сделок

   export_timer_counter++;
   if(export_timer_counter % 5 == 0) // каждые 15 секунд
   {
      CleanupStalePendingOrders();
   }

   if(export_timer_counter >= 20) // каждые 60 секунд (20 * 3 сек)
   {
      export_timer_counter = 0;
      ExportBrokerRates();
   }
}

//+------------------------------------------------------------------+
//| Получение JSON открытых позиций                                  |
//+------------------------------------------------------------------+
//+------------------------------------------------------------------+
//| Получение JSON открытых позиций (все сделки: робот + ручные)    |
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
         long magic = PositionGetInteger(POSITION_MAGIC);
         if(count > 0) json += ",";
         string sym = PositionGetString(POSITION_SYMBOL);
         ENUM_POSITION_TYPE type = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
         double volume = PositionGetDouble(POSITION_VOLUME);
         double price = PositionGetDouble(POSITION_PRICE_OPEN);
         double sl = PositionGetDouble(POSITION_SL);
         double tp = PositionGetDouble(POSITION_TP);
         double profit = PositionGetDouble(POSITION_PROFIT);
         string type_str = (type == POSITION_TYPE_BUY) ? "BUY" : "SELL";
         json += StringFormat("{\"ticket\":%I64u,\"symbol\":\"%s\",\"type\":\"%s\",\"lot\":%.2f,\"price\":%.5f,\"sl\":%.5f,\"tp\":%.5f,\"profit\":%.2f,\"magic\":%I64d,\"is_manual\":%s}",
                              ticket, sym, type_str, volume, price, sl, tp, profit, magic, (magic == 0 ? "true" : "false"));
         count++;
      }
   }
   json += "]";
   return json;
}

//+------------------------------------------------------------------+
//| Получение JSON отложенных ордеров (все ордера: робот + ручные)   |
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
         long magic = OrderGetInteger(ORDER_MAGIC);
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
         
         json += StringFormat("{\"ticket\":%I64u,\"symbol\":\"%s\",\"type\":\"%s\",\"lot\":%.2f,\"price\":%.5f,\"sl\":%.5f,\"tp\":%.5f,\"magic\":%I64d,\"is_manual\":%s}",
                              ticket, sym, type_str, volume, price, sl, tp, magic, (magic == 0 ? "true" : "false"));
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
//| Получение JSON истории закрытых сделок (робот + ручные)          |
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
         int copied = CopyRates(sym, tfs[t], 0, 350, rates);
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
   headers += "X-API-Key: " + InpApiKey + "\r\n";

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
   int bytes_copied = StringToCharArray(body, post_data, 0, WHOLE_ARRAY, CP_UTF8);
   if(bytes_copied > 0 && post_data[bytes_copied - 1] == 0)
   {
      ArrayResize(post_data, bytes_copied - 1);
   }
   
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
//| Автоматическое определение лучшего типа исполнения ордера        |
//| (Совместимо со всеми брокерами, ECN, Standard и Prop-фирмами)    |
//+------------------------------------------------------------------+
void SetOptimalFillingMode(string symbol)
{
   uint fill_modes = (uint)SymbolInfoInteger(symbol, SYMBOL_FILLING_MODE);
   
   // SYMBOL_FILLING_IOC = 2, SYMBOL_FILLING_FOK = 1
   if((fill_modes & SYMBOL_FILLING_IOC) != 0)
   {
      trade.SetTypeFilling(ORDER_FILLING_IOC);
   }
   else if((fill_modes & SYMBOL_FILLING_FOK) != 0)
   {
      trade.SetTypeFilling(ORDER_FILLING_FOK);
   }
   else
   {
      trade.SetTypeFilling(ORDER_FILLING_RETURN);
   }
}

//+------------------------------------------------------------------+
//| Авто-очистка зависших отложенных ордеров старше 24 часов         |
//+------------------------------------------------------------------+
void CleanupStalePendingOrders()
{
   datetime now = TimeCurrent();
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      ulong ticket = OrderGetTicket(i);
      if(ticket > 0 && OrderGetInteger(ORDER_MAGIC) == InpMagicNumber)
      {
         datetime time_setup = (datetime)OrderGetInteger(ORDER_TIME_SETUP);
         if(time_setup > 0 && (now - time_setup) > 86400)
         {
            string sym = OrderGetString(ORDER_SYMBOL);
            Print("⏰ [SmartTrader] Отложенный ордер #", ticket, " (", sym, ") устарел (>24ч). Авто-удаление...");
            if(trade.OrderDelete(ticket))
            {
               ReportExecution(sym, "LIMIT_EXPIRED", 0.0, 0.0, "Истек срок 24ч (авто-очистка советником)", 0, ticket);
            }
         }
      }
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

      // Извлекаем фрагмент вокруг пары (обратный поиск { от pos)
      int block_start = -1;
      for(int k = pos; k >= 0; k--)
      {
         if(StringGetCharacter(json, k) == '{')
         {
            block_start = k;
            break;
         }
      }
      int block_end = StringFind(json, "}", pos);
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
         continue;
      }

      // Проверяем, открыта ли уже позиция или отложенный ордер по этой паре с нашим Magic
      if(HasOpenPosition(broker_symbol) || HasPendingOrder(broker_symbol))
      {
         if(sig_id > 0) MarkSignalProcessed(sig_id);
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
      double tp1   = ExtractDouble(block, "\"tp1\":");
      double tp2   = ExtractDouble(block, "\"tp2\":");
      double tp    = (tp1 > 0) ? tp1 : tp2;
      if(sl <= 0 || tp <= 0) continue;

      bool is_limit = (StringFind(block, "LIMIT") >= 0);

      // Извлекаем параметры лота и риска из JSON блока ордера
      double json_lot   = ExtractDouble(block, "\"lot\":");
      double json_risk  = ExtractDouble(block, "\"risk_percent\":");
      int json_use_risk = (int)ExtractDouble(block, "\"use_auto_risk\":");
      bool is_auto_risk = (json_use_risk == 1 || (json_use_risk < 0 && InpUseAutoRisk));

      // Рассчитываем итоговый объем ордера
      double lot = (json_lot > 0.0) ? json_lot : InpFixedLot;
      if(is_auto_risk)
      {
         double entry_ref = is_limit ? entry : (is_long ? SymbolInfoDouble(broker_symbol, SYMBOL_ASK) : SymbolInfoDouble(broker_symbol, SYMBOL_BID));
         double eff_risk  = (json_risk > 0.0) ? json_risk : InpRiskPercent;
         lot = CalculateRiskLot(broker_symbol, entry_ref, sl, eff_risk, lot);
         PrintFormat("⚖️ [SmartTrader] %s: Авто-риск %.1f%% -> рассчитан лот %.2f (Баланс: %.2f)", broker_symbol, eff_risk, lot, AccountInfoDouble(ACCOUNT_BALANCE));
      }
      else
      {
         // Нормализуем фиксированный лот по требованиям брокера
         double min_l  = SymbolInfoDouble(broker_symbol, SYMBOL_VOLUME_MIN);
         double max_l  = SymbolInfoDouble(broker_symbol, SYMBOL_VOLUME_MAX);
         double step_l = SymbolInfoDouble(broker_symbol, SYMBOL_VOLUME_STEP);
         if(step_l > 0)
            lot = MathFloor(lot / step_l) * step_l;
         lot = MathMax(min_l, MathMin(max_l, lot));
         PrintFormat("📊 [SmartTrader] %s: Фиксированный лот из команды бота -> %.2f", broker_symbol, lot);
      }

      // Институциональная маржинальная защита (Margin Guard)
      double free_margin = AccountInfoDouble(ACCOUNT_MARGIN_FREE);
      if(free_margin < 50.0)
      {
         string err_margin = StringFormat("Недостаточно свободной маржи: %.2f USD (< 50.0)", free_margin);
         Print("⛔ [SmartTrader] ОТМЕНА: ", broker_symbol, ". ", err_margin);
         ReportExecution(pair, "REJECTED_MARGIN", 0.0, 0.0, err_margin, sig_id);
         if(sig_id > 0) MarkSignalProcessed(sig_id);
         continue;
      }

      // Автоматическое определение режима заполнения для брокера
      SetOptimalFillingMode(broker_symbol);

      // Открываем ордер с валидацией R:R
      if(is_long)
      {
         double ask = SymbolInfoDouble(broker_symbol, SYMBOL_ASK);
         if(is_limit && entry > 0 && entry < ask)
         {
            datetime exp_time = TimeCurrent() + 86400; // 24 часа
            ENUM_ORDER_TYPE_TIME time_type = ORDER_TIME_GTC;
            long exp_flags = SymbolInfoInteger(broker_symbol, SYMBOL_EXPIRATION_MODE);
            if((exp_flags & SYMBOL_EXPIRATION_SPECIFIED) != 0)
            {
               time_type = ORDER_TIME_SPECIFIED;
            }

            if(trade.BuyLimit(lot, entry, broker_symbol, sl, tp, time_type, (time_type == ORDER_TIME_SPECIFIED ? exp_time : 0), "SmartTrader Limit"))
            {
               ulong order_ticket = trade.ResultOrder();
               Print("✅ [SmartTrader] BUY_LIMIT выставлен: ", broker_symbol, " #", order_ticket, " @ ", entry, " | SL: ", sl, " | TP: ", tp, " | Срок: 24ч");
               ReportExecution(pair, "BUY_LIMIT", entry, 0.0, "Buy Limit выставлен (24ч)", sig_id, order_ticket);
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
            // Проверка: цена не должна быть выше TP
            if(tp <= ask)
            {
               string err_tp = StringFormat("Цена уже ушла за TP (Ask=%.5f >= TP=%.5f)", ask, tp);
               Print("⛔ [SmartTrader] ОТМЕНА: ", broker_symbol, ". ", err_tp);
               ReportExecution(pair, "REJECTED_ERROR", ask, 0.0, err_tp, sig_id);
               if(sig_id > 0) MarkSignalProcessed(sig_id);
               continue;
            }

            // Проверка реального R:R от рыночной цены перед входом по рынку
            double real_risk = MathAbs(ask - sl);
            double real_reward = tp - ask; // Строго положительный
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
            double spread_points = (double)SymbolInfoInteger(broker_symbol, SYMBOL_SPREAD);
            double point_val = SymbolInfoDouble(broker_symbol, SYMBOL_POINT);
            double spread_price = spread_points * point_val;
            double max_spread = real_risk * 0.15; // Spread should not exceed 15% of risk
            if(spread_price > max_spread && max_spread > 0)
            {
               string spread_msg = StringFormat("Спред %.1f п. превышает лимит (15%% от риска)", spread_points);
               Print("⚠️ [SmartTrader] Spread too high for ", broker_symbol, ": ", spread_price, " > max ", max_spread);
               ReportExecution(pair, "REJECTED_SPREAD", ask, 0.0, spread_msg, sig_id);
               if(sig_id > 0) MarkSignalProcessed(sig_id);
               continue;
            }

            if(trade.Buy(lot, broker_symbol, ask, sl, tp, "SmartTrader Institutional"))
            {
               ulong order_ticket = trade.ResultOrder();
               ulong deal_ticket = trade.ResultDeal();
               ulong t_report = (order_ticket > 0) ? order_ticket : deal_ticket;
               double exec_p = trade.ResultPrice();
               if(exec_p <= 0) exec_p = ask;
               double slip = MathAbs(exec_p - ask) / (point_val > 0 ? point_val : 0.0001);
               Print("✅ [SmartTrader] BUY ордер открыт: ", broker_symbol, " #", t_report, " @ ", exec_p, " (Проскальзывание: ", DoubleToString(slip, 1), " п.) | Лот: ", lot, " | SL: ", sl, " | TP: ", tp, " | R:R: 1:", DoubleToString(real_rr, 2));
               ReportExecution(pair, "BUY", exec_p, 0.0, StringFormat("Лот %.2f (Slip %.1f pt)", lot, slip), sig_id, t_report);
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
            datetime exp_time = TimeCurrent() + 86400; // 24 часа
            ENUM_ORDER_TYPE_TIME time_type = ORDER_TIME_GTC;
            long exp_flags = SymbolInfoInteger(broker_symbol, SYMBOL_EXPIRATION_MODE);
            if((exp_flags & SYMBOL_EXPIRATION_SPECIFIED) != 0)
            {
               time_type = ORDER_TIME_SPECIFIED;
            }

            if(trade.SellLimit(lot, entry, broker_symbol, sl, tp, time_type, (time_type == ORDER_TIME_SPECIFIED ? exp_time : 0), "SmartTrader Limit"))
            {
               ulong order_ticket = trade.ResultOrder();
               Print("✅ [SmartTrader] SELL_LIMIT выставлен: ", broker_symbol, " #", order_ticket, " @ ", entry, " | SL: ", sl, " | TP: ", tp, " | Срок: 24ч");
               ReportExecution(pair, "SELL_LIMIT", entry, 0.0, "Sell Limit выставлен (24ч)", sig_id, order_ticket);
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
            // Проверка: цена не должна быть ниже TP
            if(tp >= bid)
            {
               string err_tp = StringFormat("Цена уже ушла за TP (Bid=%.5f <= TP=%.5f)", bid, tp);
               Print("⛔ [SmartTrader] ОТМЕНА: ", broker_symbol, ". ", err_tp);
               ReportExecution(pair, "REJECTED_ERROR", bid, 0.0, err_tp, sig_id);
               if(sig_id > 0) MarkSignalProcessed(sig_id);
               continue;
            }

            // Проверка реального R:R от рыночной цены перед входом по рынку
            double real_risk = MathAbs(sl - bid);
            double real_reward = bid - tp; // Строго положительный
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
            double spread_points = (double)SymbolInfoInteger(broker_symbol, SYMBOL_SPREAD);
            double point_val = SymbolInfoDouble(broker_symbol, SYMBOL_POINT);
            double spread_price = spread_points * point_val;
            double max_spread = real_risk * 0.15; // Spread should not exceed 15% of risk
            if(spread_price > max_spread && max_spread > 0)
            {
               string spread_msg = StringFormat("Спред %.1f п. превышает лимит (15%% от риска)", spread_points);
               Print("⚠️ [SmartTrader] Spread too high for ", broker_symbol, ": ", spread_price, " > max ", max_spread);
               ReportExecution(pair, "REJECTED_SPREAD", bid, 0.0, spread_msg, sig_id);
               if(sig_id > 0) MarkSignalProcessed(sig_id);
               continue;
            }

            if(trade.Sell(lot, broker_symbol, bid, sl, tp, "SmartTrader Institutional"))
            {
               ulong order_ticket = trade.ResultOrder();
               ulong deal_ticket = trade.ResultDeal();
               ulong t_report = (order_ticket > 0) ? order_ticket : deal_ticket;
               double exec_p = trade.ResultPrice();
               if(exec_p <= 0) exec_p = bid;
               double slip = MathAbs(exec_p - bid) / (point_val > 0 ? point_val : 0.0001);
               Print("✅ [SmartTrader] SELL ордер открыт: ", broker_symbol, " #", t_report, " @ ", exec_p, " (Проскальзывание: ", DoubleToString(slip, 1), " п.) | Лот: ", lot, " | SL: ", sl, " | TP: ", tp, " | R:R: 1:", DoubleToString(real_rr, 2));
               ReportExecution(pair, "SELL", exec_p, 0.0, StringFormat("Лот %.2f (Slip %.1f pt)", lot, slip), sig_id, t_report);
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
         long magic = PositionGetInteger(POSITION_MAGIC);
         if(magic == InpMagicNumber || magic == 888001 || magic == 777001 || magic == 0)
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
         long magic = OrderGetInteger(ORDER_MAGIC);
         if(magic == InpMagicNumber || magic == 888001 || magic == 777001 || magic == 0)
            return true;
      }
   }
   return false;
}

//+------------------------------------------------------------------+
//| Подсчет всех открытых позиций и отложенных ордеров (всего счёта)|
//+------------------------------------------------------------------+
int CountTotalOpenAndPending()
{
   int count = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      long magic = PositionGetInteger(POSITION_MAGIC);
      if(magic == InpMagicNumber || magic == 888001 || magic == 777001 || magic == 0)
         count++;
   }
   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      ulong ticket = OrderGetTicket(i);
      if(ticket > 0)
      {
         long magic = OrderGetInteger(ORDER_MAGIC);
         if(magic == InpMagicNumber || magic == 888001 || magic == 777001 || magic == 0)
            count++;
      }
   }
   return count;
}

//+------------------------------------------------------------------+
//| Синхронизация SL открытой позиции с сервером / безубыток        |
//+------------------------------------------------------------------+
void SyncPositionSL(string symbol, double target_sl)
{
   if(target_sl <= 0) return;
   SymbolSelect(symbol, true);
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(StringCompare(PositionGetSymbol(i), symbol, false) == 0 && PositionGetInteger(POSITION_MAGIC) == InpMagicNumber)
      {
         ulong ticket = PositionGetTicket(i);
         if(ticket <= 0) continue;

         double curr_sl    = PositionGetDouble(POSITION_SL);
         double tp         = PositionGetDouble(POSITION_TP);
         ENUM_POSITION_TYPE pos_type = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);

         double point = SymbolInfoDouble(symbol, SYMBOL_POINT);
         long stops_level = SymbolInfoInteger(symbol, SYMBOL_TRADE_STOPS_LEVEL);
         double min_stop_dist = stops_level * point;

         MqlTick tick;
         if(!SymbolInfoTick(symbol, tick)) continue;

         bool can_modify = false;
         if(pos_type == POSITION_TYPE_BUY)
         {
            // Для BUY: новый SL должен быть выше текущего и ниже цены Bid на min_stop_dist
            if((curr_sl == 0 || target_sl > curr_sl + point) && tick.bid > target_sl + min_stop_dist)
               can_modify = true;
            else if(target_sl <= curr_sl)
               continue;
            else
               Print("⏳ [SmartTrader SL] ", symbol, " BUY: текущий Bid ", tick.bid, " слишком близко к новому SL ", target_sl, " (отступ >= ", min_stop_dist, ")");
         }
         else if(pos_type == POSITION_TYPE_SELL)
         {
            // Для SELL: новый SL должен быть ниже текущего и выше цены Ask на min_stop_dist
            if((curr_sl == 0 || target_sl < curr_sl - point) && tick.ask < target_sl - min_stop_dist)
               can_modify = true;
            else if(curr_sl > 0 && target_sl >= curr_sl)
               continue;
            else
               Print("⏳ [SmartTrader SL] ", symbol, " SELL: текущий Ask ", tick.ask, " слишком близко к новому SL ", target_sl, " (отступ >= ", min_stop_dist, ")");
         }

         if(can_modify)
         {
            if(trade.PositionModify(ticket, target_sl, tp))
            {
               Print("🛡 [SmartTrader] Позиция ", symbol, " #", ticket, " SL успешно подтянут на: ", target_sl, " (профит зафиксирован)");
               ReportExecution(symbol, "SL_UPDATED", target_sl, 0.0, StringFormat("SL зафиксирован на %.2f", target_sl), 0, ticket);
            }
            else
            {
               Print("⚠️ [SmartTrader] Ошибка модификации SL #", ticket, ": ", trade.ResultRetcode(), " - ", trade.ResultRetcodeDescription());
            }
         }
      }
   }
}

void ApplyBreakevenIfEligible(string symbol)
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      if(StringCompare(PositionGetSymbol(i), symbol, false) == 0 && PositionGetInteger(POSITION_MAGIC) == InpMagicNumber)
      {
         ulong ticket = PositionGetTicket(i);
         if(ticket <= 0) continue;
         double open_price = PositionGetDouble(POSITION_PRICE_OPEN);
         SyncPositionSL(symbol, open_price);
      }
   }
}

//+------------------------------------------------------------------+
//| Институциональный менеджер открытых позиций (Auto-BE & Partials) |
//+------------------------------------------------------------------+
void ManageOpenPositions()
{
   if(!InpUseAutoBE) return;

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket <= 0) continue;
      long magic = PositionGetInteger(POSITION_MAGIC);
      if(magic != InpMagicNumber && magic != 777001 && magic != 0) continue;

      string symbol = PositionGetString(POSITION_SYMBOL);
      double open_price = PositionGetDouble(POSITION_PRICE_OPEN);
      double curr_sl    = PositionGetDouble(POSITION_SL);
      double tp         = PositionGetDouble(POSITION_TP);
      double volume     = PositionGetDouble(POSITION_VOLUME);
      double profit     = PositionGetDouble(POSITION_PROFIT);
      ENUM_POSITION_TYPE pos_type = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);

      SymbolSelect(symbol, true);
      double point = SymbolInfoDouble(symbol, SYMBOL_POINT);
      long stops_level = SymbolInfoInteger(symbol, SYMBOL_TRADE_STOPS_LEVEL);
      double min_stop_dist = stops_level * point;

      MqlTick tick;
      if(!SymbolInfoTick(symbol, tick)) continue;

      // 1. Проверка условия перевода в безубыток:
      // Переводим в безубыток строго при достижении +1.0R (профит >= первоначальному риску)
      bool qualify_be = false;
      double new_be_sl = 0.0;

      if(pos_type == POSITION_TYPE_BUY)
      {
         // Если SL уже на уровне входа или выше - уже защищено
         if(curr_sl >= open_price - point) continue;

         double initial_risk = open_price - curr_sl;
         double current_run   = tick.bid - open_price;

         if(curr_sl > 0 && initial_risk > 0 && current_run >= initial_risk * 1.0)
            qualify_be = true;

         if(qualify_be && tick.bid > open_price + min_stop_dist)
         {
            new_be_sl = open_price + 2 * point;
            if(trade.PositionModify(ticket, new_be_sl, tp))
            {
               Print("🛡 [SmartTrader Auto-BE] BUY ", symbol, " #", ticket, " защищен в БЕЗУБЫТОК (+1.0R)! SL: ", new_be_sl, " | Профит: $", DoubleToString(profit, 2));
               ReportExecution(symbol, "AUTO_BREAKEVEN", tick.bid, profit, "Авто-безубыток (+1.0R защищен)", 0, ticket);

               // Частичная фиксация прибыли (если объем >= 0.02)
               double step = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
               double min_vol = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
               double half_lot = MathFloor((volume * 0.5) / step) * step;
               if(half_lot >= min_vol && (volume - half_lot) >= min_vol)
               {
                  if(trade.PositionClosePartial(ticket, half_lot))
                  {
                     Print("💰 [SmartTrader Partial] Закрыто 50% объема (", half_lot, " лота) по ", symbol, " #", ticket);
                     ReportExecution(symbol, "PARTIAL_CLOSE", tick.bid, profit * 0.5, StringFormat("Фиксация 50%% (%.2f лот)", half_lot), 0, ticket);
                  }
               }
            }
         }
      }
      else if(pos_type == POSITION_TYPE_SELL)
      {
         // Если SL уже на уровне входа или ниже - уже защищено
         if(curr_sl > 0 && curr_sl <= open_price + point) continue;

         double initial_risk = curr_sl - open_price;
         double current_run   = open_price - tick.ask;

         if(curr_sl > 0 && initial_risk > 0 && current_run >= initial_risk * 1.0)
            qualify_be = true;

         if(qualify_be && tick.ask < open_price - min_stop_dist)
         {
            new_be_sl = open_price - 2 * point;
            if(trade.PositionModify(ticket, new_be_sl, tp))
            {
               Print("🛡 [SmartTrader Auto-BE] SELL ", symbol, " #", ticket, " защищен в БЕЗУБЫТОК (+1.0R)! SL: ", new_be_sl, " | Профит: $", DoubleToString(profit, 2));
               ReportExecution(symbol, "AUTO_BREAKEVEN", tick.ask, profit, "Авто-безубыток (+1.0R защищен)", 0, ticket);

               double step = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
               double min_vol = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
               double half_lot = MathFloor((volume * 0.5) / step) * step;
               if(half_lot >= min_vol && (volume - half_lot) >= min_vol)
               {
                  if(trade.PositionClosePartial(ticket, half_lot))
                  {
                     Print("💰 [SmartTrader Partial] Закрыто 50% объема (", half_lot, " лота) по ", symbol, " #", ticket);
                     ReportExecution(symbol, "PARTIAL_CLOSE", tick.ask, profit * 0.5, StringFormat("Фиксация 50%% (%.2f лот)", half_lot), 0, ticket);
                  }
               }
            }
         }
      }
   }
}

//+------------------------------------------------------------------+
//| Расчет объема лота на основе % риска от баланса                 |
//+------------------------------------------------------------------+
double CalculateRiskLot(string symbol, double entry_price, double sl_price, double custom_risk = 0.0, double fallback_lot = 0.01)
{
   double balance   = AccountInfoDouble(ACCOUNT_BALANCE);
   double risk_pct  = (custom_risk > 0.0) ? custom_risk : InpRiskPercent;
   double risk_amt  = balance * (risk_pct / 100.0);
   double diff      = MathAbs(entry_price - sl_price);
   double def_lot   = (fallback_lot > 0.0) ? fallback_lot : InpFixedLot;
   if(diff <= 0) return def_lot;
   
   double tick_size = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE);
   double tick_val  = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE);
   if(tick_size <= 0 || tick_val <= 0) return def_lot;
   
   double pips = diff / tick_size;
   double lot = risk_amt / (pips * tick_val);
   
   double min_lot = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
   double max_lot = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX);
   double step    = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
   
   if(step > 0)
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
void ReportExecution(string symbol, string action, double price, double profit=0.0, string reason="", int signal_id=0, ulong ticket=0)
{
   string url = InpServerUrl + "/api/v1/bridge/report";
   string headers = "Content-Type: application/json\r\n";
   headers += "X-API-Key: " + InpApiKey + "\r\n";
   string esc_reason = reason;
   StringReplace(esc_reason, "\"", "'");
   StringReplace(esc_reason, "\r", " ");
   StringReplace(esc_reason, "\n", " ");
   string body = StringFormat("{\"symbol\":\"%s\",\"action\":\"%s\",\"price\":%.5f,\"profit\":%.2f,\"reason\":\"%s\",\"signal_id\":%d,\"ticket\":%I64u}", 
                              symbol, action, price, profit, esc_reason, signal_id, ticket);
   char post_data[];
   char result_data[];
   string result_headers;
   int bytes_copied = StringToCharArray(body, post_data, 0, WHOLE_ARRAY, CP_UTF8);
   if(bytes_copied > 0 && post_data[bytes_copied - 1] == 0)
   {
      ArrayResize(post_data, bytes_copied - 1);
   }
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
         ENUM_DEAL_ENTRY entry_type = (ENUM_DEAL_ENTRY)HistoryDealGetInteger(deal_ticket, DEAL_ENTRY);
         ENUM_DEAL_REASON reason = (ENUM_DEAL_REASON)HistoryDealGetInteger(deal_ticket, DEAL_REASON);
         string sym = HistoryDealGetString(deal_ticket, DEAL_SYMBOL);
         double price = HistoryDealGetDouble(deal_ticket, DEAL_PRICE);
         double profit = HistoryDealGetDouble(deal_ticket, DEAL_PROFIT);
         double volume = HistoryDealGetDouble(deal_ticket, DEAL_VOLUME);
         string comment = HistoryDealGetString(deal_ticket, DEAL_COMMENT);
         ENUM_DEAL_TYPE dtype = (ENUM_DEAL_TYPE)HistoryDealGetInteger(deal_ticket, DEAL_TYPE);
         string dir_str = (dtype == DEAL_TYPE_BUY) ? "BUY" : "SELL";

         if(magic == InpMagicNumber || magic == 888001 || magic == 777001)
         {
            if(entry_type == DEAL_ENTRY_OUT || entry_type == DEAL_ENTRY_INOUT)
            {
               string lower_comment = comment;
               StringToLower(lower_comment);
               bool is_tp = (reason == DEAL_REASON_TP) || (StringFind(lower_comment, "tp") >= 0) || (StringFind(lower_comment, "take") >= 0);
               bool is_sl = (reason == DEAL_REASON_SL) || (StringFind(lower_comment, "sl") >= 0) || (StringFind(lower_comment, "stop") >= 0);
               bool is_manual_client = (reason == DEAL_REASON_CLIENT || reason == DEAL_REASON_MOBILE || reason == DEAL_REASON_WEB);

               if(is_manual_client && !is_tp && !is_sl)
               {
                  Print("👑 [SmartTrader Bridge] Пользователь вручную закрыл сделку робота ", sym, " #", deal_ticket, " | Профит: ", profit);
                  ReportExecution(sym, "MANUAL_CLOSE", price, profit, "MANUAL_CLIENT_CLOSE", 0, deal_ticket);
               }
               else if(is_tp)
               {
                  Print("🏆 [SmartTrader Bridge] Тейк-профит сработал: ", sym, " #", deal_ticket, " | Профит: ", profit);
                  ReportExecution(sym, "DEAL_CLOSED", price, profit, "TP_HIT", 0, deal_ticket);
               }
               else if(is_sl)
               {
                  Print("🛑 [SmartTrader Bridge] Стоп-лосс сработал: ", sym, " #", deal_ticket, " | Убыток: ", profit);
                  ReportExecution(sym, "DEAL_CLOSED", price, profit, "SL_HIT", 0, deal_ticket);
               }
               else
               {
                  Print("📢 [SmartTrader Bridge] Закрыта сделка ", sym, " #", deal_ticket, " | Профит: ", profit, " USD | Цена: ", price);
                  ReportExecution(sym, "DEAL_CLOSED", price, profit, comment, 0, deal_ticket);
               }
            }
            else if(entry_type == DEAL_ENTRY_IN)
            {
               ulong order_ticket = HistoryDealGetInteger(deal_ticket, DEAL_ORDER);
               Print("🚀 [SmartTrader Bridge] Лимитный ордер сработал (ORDER_FILLED): ", sym, " #", deal_ticket, " order #", order_ticket, " @ ", price);
               ReportExecution(sym, "ORDER_FILLED", price, 0.0, comment, 0, (order_ticket > 0 ? order_ticket : deal_ticket));
            }
         }
         else
         {
            // Ручные действия пользователя в терминале MT5
            if(entry_type == DEAL_ENTRY_IN)
            {
               Print("👑 [SmartTrader Bridge] Пользователь открыл сделку вручную: ", sym, " ", dir_str, " лот ", volume, " @ ", price);
               ReportExecution(sym, "MANUAL_OPEN", price, volume, dir_str, 0, deal_ticket);
            }
            else if(entry_type == DEAL_ENTRY_OUT || entry_type == DEAL_ENTRY_INOUT)
            {
               Print("👑 [SmartTrader Bridge] Пользователь закрыл сделку вручную: ", sym, " | Профит: ", profit, " USD @ ", price);
               ReportExecution(sym, "MANUAL_CLOSE", price, profit, "MANUAL_CLIENT_CLOSE", 0, deal_ticket);
            }
         }
      }
   }
   else if(trans.type == TRADE_TRANSACTION_HISTORY_ADD)
   {
      ulong order_ticket = trans.order;
      if(order_ticket > 0 && HistoryOrderSelect(order_ticket))
      {
         long magic = HistoryOrderGetInteger(order_ticket, ORDER_MAGIC);
         if(magic == InpMagicNumber)
         {
            ENUM_ORDER_STATE state = (ENUM_ORDER_STATE)HistoryOrderGetInteger(order_ticket, ORDER_STATE);
            ENUM_ORDER_TYPE otype = (ENUM_ORDER_TYPE)HistoryOrderGetInteger(order_ticket, ORDER_TYPE);
            if(otype == ORDER_TYPE_BUY_LIMIT || otype == ORDER_TYPE_SELL_LIMIT)
            {
               if(state == ORDER_STATE_EXPIRED || state == ORDER_STATE_CANCELED)
               {
                  string sym = HistoryOrderGetString(order_ticket, ORDER_SYMBOL);
                  Print("⏰ [SmartTrader Bridge] Лимитный ордер удален/истек (LIMIT_EXPIRED): ", sym, " #", order_ticket);
                  ReportExecution(sym, "LIMIT_EXPIRED", 0.0, 0.0, "LIMIT_EXPIRED_OR_CANCELED", 0, order_ticket);
               }
            }
         }
      }
   }
}


