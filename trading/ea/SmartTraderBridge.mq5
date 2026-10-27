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
input string   InpApiKey       = "stb-fp-9f8e7d6c5b4a3f2e1d0c-2026"; // API Key for Bridge
input int      InpPollInterval = 3;                       // Опрос сервера каждые N секунд
input ulong    InpMagicNumber  = 888001;                   // Magic Number ордеров

input group "=== УПРАВЛЕНИЕ РИСКОМ ==="
input int      InpMaxOpenOrders = 1;                       // Макс. одновременно активных позиций/ордеров (строго 1 для Золота)
input bool     InpUseAutoRisk  = false;                    // Использовать расчет лота от баланса (%)
input double   InpRiskPercent  = 0.8;                      // Процент риска на сделку (%) (0.8% - 1.0%)
input double   InpFixedLot     = 0.02;                     // Фиксированный лот (базовый 0.02 для XAUUSD)
input int      InpSlippage     = 20;                       // Проскальзывание в пунктах
input bool     InpUseAutoBE    = true;                    // Структурный Авто-Б/У (перенос SL за Protected Swing после 1.0R)
input int      InpBETriggerPips = 2000;                   // Минимальный профит для Б/У (+2000 п. по 0.01 = +$20.00 / 1.0R)
input int      InpBEBufferPips  = 200;                    // Защитное смещение стопа в плюс от входа (+200 п. по 0.01 = +$2.00)
input bool     InpUsePartialClose = true;                 // Частичное закрытие 50% на TP1 (Opposing Liquidity Pool)
input double   InpPartialClosePct = 50.0;                 // Процент закрытия объема на TP1 (%)

//--- Глобальные переменные
ulong    processed_signals[];

struct PositionState
{
   ulong  ticket;
   string symbol;
   ENUM_POSITION_TYPE type;
   double open_price;
   double initial_sl;
   double tp1;
   double tp2;
   double initial_volume;
   bool   partial_done;
   bool   be_done;
   bool   rollover_sl_done;
};

PositionState pos_states[];

int FindPositionState(ulong ticket)
{
   for(int i = 0; i < ArraySize(pos_states); i++)
   {
      if(pos_states[i].ticket == ticket) return i;
   }
   return -1;
}

int GetOrCreatePositionState(ulong ticket, string sym, ENUM_POSITION_TYPE ptype, double open_p, double sl_p, double vol)
{
   int idx = FindPositionState(ticket);
   if(idx >= 0) return idx;
   int sz = ArraySize(pos_states);
   ArrayResize(pos_states, sz + 1);
   pos_states[sz].ticket = ticket;
   pos_states[sz].symbol = sym;
   pos_states[sz].type = ptype;
   pos_states[sz].open_price = open_p;
   pos_states[sz].initial_sl = sl_p;
   pos_states[sz].tp1 = 0.0;
   pos_states[sz].tp2 = 0.0;
   pos_states[sz].initial_volume = vol;
   pos_states[sz].partial_done = false;
   pos_states[sz].be_done = false;
   return sz;
}

void RegisterSignalTargets(ulong ticket, double t1, double t2, double sl_val)
{
   if(ticket <= 0) return;
   int idx = FindPositionState(ticket);
   if(idx >= 0)
   {
      if(t1 > 0) pos_states[idx].tp1 = t1;
      if(t2 > 0) pos_states[idx].tp2 = t2;
      if(sl_val > 0 && pos_states[idx].initial_sl <= 0) pos_states[idx].initial_sl = sl_val;
   }
   else
   {
      int sz = ArraySize(pos_states);
      ArrayResize(pos_states, sz + 1);
      pos_states[sz].ticket = ticket;
      pos_states[sz].tp1 = t1;
      pos_states[sz].tp2 = t2;
      pos_states[sz].initial_sl = sl_val;
      pos_states[sz].partial_done = false;
      pos_states[sz].be_done = false;
   }
}

void CleanupPositionStates()
{
   for(int i = ArraySize(pos_states) - 1; i >= 0; i--)
   {
      bool exists = false;
      for(int p = 0; p < PositionsTotal(); p++)
      {
         if(PositionGetTicket(p) == pos_states[i].ticket)
         {
            exists = true;
            break;
         }
      }
      if(!exists)
      {
         for(int o = 0; o < OrdersTotal(); o++)
         {
            if(OrderGetTicket(o) == pos_states[i].ticket)
            {
               exists = true;
               break;
            }
         }
      }
      if(!exists)
      {
         int sz = ArraySize(pos_states);
         for(int j = i; j < sz - 1; j++)
            pos_states[j] = pos_states[j+1];
         ArrayResize(pos_states, sz - 1);
      }
   }
}

string   common_pairs[] = {
   "XAUUSD"
};

string GetBrokerSymbol(string standard_pair);
void   ReportExecution(string symbol, string action, double price, double profit=0.0, string reason="", int signal_id=0, ulong ticket=0, double volume=0.0);
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
void ManageOpenPositions();
double FindProtectedSwingPrice(string symbol, ENUM_POSITION_TYPE pos_type, double open_price, datetime pos_time, double point, int digits);

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
   ENUM_TIMEFRAMES tfs[6] = {PERIOD_M5, PERIOD_M15, PERIOD_H1, PERIOD_H4, PERIOD_D1, PERIOD_W1};
   string tf_names[6] = {"M5", "M15", "H1", "H4", "D1", "W1"};
   
   for(int p = 0; p < ArraySize(common_pairs); p++)
   {
      string standard_sym = common_pairs[p];
      string sym = GetBrokerSymbol(standard_sym);
      int digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
      if(digits <= 0) digits = 2;
      for(int t = 0; t < 6; t++)
      {
         MqlRates rates[];
         ArraySetAsSeries(rates, true);
         int max_req = (t == 0) ? 50000 : ((t == 1) ? 35000 : ((t == 2) ? 15000 : ((t == 3) ? 5000 : ((t == 4) ? 2000 : 500))));
         int avail = iBars(sym, tfs[t]);
         int n_bars = (avail > 0) ? MathMin(avail, max_req) : max_req;
         int copied = CopyRates(sym, tfs[t], 0, n_bars, rates);
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
                     DoubleToString(rates[k].open, digits),
                     DoubleToString(rates[k].high, digits),
                     DoubleToString(rates[k].low, digits),
                     DoubleToString(rates[k].close, digits),
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

   string body = StringFormat("{\"balance\":%.2f,\"equity\":%.2f,\"margin_free\":%.2f,\"broker\":\"%s\",\"account\":\"%I64d\",\"server_time\":%I64d,\"positions\":%s,\"orders\":%s,\"quotes\":%s,\"history\":%s}",
                              balance, equity, margin_free, esc_broker, account, (long)TimeCurrent(), pos_json, ord_json, quotes_json, history_json);

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
   MqlDateTime dt;
   TimeToStruct(now, dt);
   bool is_rollover = ((dt.hour == 23 && dt.min >= 40) || (dt.hour == 0 && dt.min <= 20));

   for(int i = OrdersTotal() - 1; i >= 0; i--)
   {
      ulong ticket = OrderGetTicket(i);
      if(ticket > 0 && OrderGetInteger(ORDER_MAGIC) == InpMagicNumber)
      {
         string sym = OrderGetString(ORDER_SYMBOL);
         datetime time_setup = (datetime)OrderGetInteger(ORDER_TIME_SETUP);
         if(is_rollover)
         {
            Print("🛡 [SmartTrader Rollover Guard] Удаление неактивированного лимитного ордера #", ticket, " (", sym, ") перед банковским ролловером (защита от 10x спреда)...");
            if(trade.OrderDelete(ticket))
            {
               ReportExecution(sym, "LIMIT_EXPIRED", 0.0, 0.0, "Снят перед ночным ролловером (защита от спреда)", 0, ticket);
            }
            continue;
         }
         if(time_setup > 0 && (now - time_setup) > 86400)
         {
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
      int eff_max_orders = (InpMaxOpenOrders > 0 && InpMaxOpenOrders <= 1) ? InpMaxOpenOrders : 1;
      if(CountTotalOpenAndPending() >= eff_max_orders)
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
      double tp    = (tp2 > 0) ? tp2 : tp1;
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

      // Single source of truth: validate minimum broker lot
      double min_vol = SymbolInfoDouble(broker_symbol, SYMBOL_VOLUME_MIN);
      if(lot < min_vol)
      {
         PrintFormat("⛔ [SmartTrader] ОТМЕНА: рассчитанный лот %.4f ниже мин. лота %.2f (риск превышает бюджет)!", lot, min_vol);
         ReportExecution(pair, "REJECTED_RISK", entry, 0.0, "Risk exceeds budget at min lot", sig_id);
         if(sig_id > 0) MarkSignalProcessed(sig_id);
         continue;
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
               RegisterSignalTargets(order_ticket, tp1, tp2, sl);
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
            double min_req_rr = (StringFind(broker_symbol, "XAU") >= 0 || StringFind(broker_symbol, "GOLD") >= 0) ? 2.45 : 1.38;
            if(real_rr < min_req_rr)
            {
               string rr_msg = StringFormat("Реальный R:R 1:%.2f < 1:%.2f (Защита капитала)", real_rr, min_req_rr);
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
               RegisterSignalTargets(t_report, tp1, tp2, sl);
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
               RegisterSignalTargets(order_ticket, tp1, tp2, sl);
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
            double min_req_rr = (StringFind(broker_symbol, "XAU") >= 0 || StringFind(broker_symbol, "GOLD") >= 0) ? 2.45 : 1.38;
            if(real_rr < min_req_rr)
            {
               string rr_msg = StringFormat("Реальный R:R 1:%.2f < 1:%.2f (Защита капитала)", real_rr, min_req_rr);
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
               RegisterSignalTargets(t_report, tp1, tp2, sl);
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
      ulong ticket = PositionGetTicket(i);
      if(ticket > 0)
      {
         long magic = PositionGetInteger(POSITION_MAGIC);
         if(magic == InpMagicNumber || magic == 888001 || magic == 777001 || magic == 0)
            count++;
      }
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
      datetime pos_time = (datetime)PositionGetInteger(POSITION_TIME);

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
//| Поиск защитного локального свинга (Protected Low/High) на M5     |
//+------------------------------------------------------------------+
double FindProtectedSwingPrice(string symbol, ENUM_POSITION_TYPE pos_type, double open_price, datetime pos_time, double point, int digits)
{
   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   int copied = CopyRates(symbol, PERIOD_H1, 1, 40, rates);
   if(copied < 5) return 0.0;

   bool is_gold = (StringFind(symbol, "XAU") >= 0);
   double buffer = is_gold ? 1.00 : ((digits == 3 || digits == 5) ? (20.0 * point) : (2.0 * point));

   if(pos_type == POSITION_TYPE_BUY)
   {
      double protected_low = 0.0;
      for(int k = 1; k < copied - 1; k++)
      {
         if(pos_time > 0 && rates[k].time < (pos_time - 7200))
            break;

         if(rates[k].low < rates[k-1].low && rates[k].low < rates[k+1].low)
         {
            if(rates[k].low > open_price)
            {
               protected_low = rates[k].low;
               break;
            }
         }
      }
      if(protected_low > open_price)
         return NormalizeDouble(protected_low - buffer, digits);
      return 0.0;
   }
   else // POSITION_TYPE_SELL
   {
      double protected_high = 0.0;
      for(int k = 1; k < copied - 1; k++)
      {
         if(pos_time > 0 && rates[k].time < (pos_time - 7200))
            break;

         if(rates[k].high > rates[k-1].high && rates[k].high > rates[k+1].high)
         {
            if(rates[k].high < open_price)
            {
               protected_high = rates[k].high;
               break;
            }
         }
      }
      if(protected_high > 0.0 && protected_high < open_price)
         return NormalizeDouble(protected_high + buffer, digits);
      return 0.0;
   }
}

//+------------------------------------------------------------------+
//| Институциональный менеджер открытых позиций (Auto-BE & Partials) |
//+------------------------------------------------------------------+
void ManageOpenPositions()
{
   CleanupPositionStates();

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket <= 0) continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagicNumber) continue;

      string symbol     = PositionGetString(POSITION_SYMBOL);
      double open_price = PositionGetDouble(POSITION_PRICE_OPEN);
      double curr_sl    = PositionGetDouble(POSITION_SL);
      double tp         = PositionGetDouble(POSITION_TP);
      double volume     = PositionGetDouble(POSITION_VOLUME);
      double profit     = PositionGetDouble(POSITION_PROFIT);
      ENUM_POSITION_TYPE pos_type = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
      datetime pos_time = (datetime)PositionGetInteger(POSITION_TIME);

      SymbolSelect(symbol, true);
      double point = SymbolInfoDouble(symbol, SYMBOL_POINT);
      int digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
      double pip_size = (digits == 3 || digits == 5) ? (10.0 * point) : point;
      long stops_level = SymbolInfoInteger(symbol, SYMBOL_TRADE_STOPS_LEVEL);
      double min_stop_dist = MathMax(stops_level * point, 10.0 * point);

      MqlTick tick;
      if(!SymbolInfoTick(symbol, tick)) continue;

      int state_idx = GetOrCreatePositionState(ticket, symbol, pos_type, open_price, curr_sl, volume);
      if(pos_states[state_idx].initial_sl <= 0.0 && curr_sl > 0.0)
         pos_states[state_idx].initial_sl = curr_sl;

      bool is_gold = (StringFind(symbol, "XAU") >= 0);
      double be_trigger_dist = is_gold ? 20.0 : ((double)InpBETriggerPips * pip_size);
      double be_offset_dist  = is_gold ? 2.0  : ((double)InpBEBufferPips * pip_size);

      double init_sl = pos_states[state_idx].initial_sl;
      double risk_dist = (init_sl > 0.0) ? MathAbs(open_price - init_sl) : be_trigger_dist;
      if(risk_dist < be_trigger_dist)
         risk_dist = be_trigger_dist;

      // Определение цели TP1 (Opposing Liquidity Pool)
      double eff_tp1 = pos_states[state_idx].tp1;
      if(eff_tp1 <= 0.0)
      {
         if(pos_type == POSITION_TYPE_BUY)
            eff_tp1 = (tp > open_price) ? MathMin(open_price + 1.5 * risk_dist, open_price + 0.6 * (tp - open_price)) : (open_price + 1.5 * risk_dist);
         else
            eff_tp1 = (tp > 0.0 && tp < open_price) ? MathMax(open_price - 1.5 * risk_dist, open_price - 0.6 * (open_price - tp)) : (open_price - 1.5 * risk_dist);
         pos_states[state_idx].tp1 = eff_tp1;
      }

      // ─────────────────────────────────────────────────────────────
      // 0. MIDNIGHT ROLLOVER GUARD (23:30 - 23:49 Server Time)
      datetime now_curr = TimeCurrent();
      MqlDateTime dt_curr;
      TimeToStruct(now_curr, dt_curr);
      bool is_rollover_guard_time = (dt_curr.hour == 23 && dt_curr.min >= 30 && dt_curr.min < 50);
      bool is_night_clearing = ((dt_curr.hour == 23 && dt_curr.min >= 50) || (dt_curr.hour == 0) || (dt_curr.hour == 1 && dt_curr.min <= 10));

      if(is_gold && is_rollover_guard_time && profit >= 80.0 && !pos_states[state_idx].rollover_sl_done)
      {
         double safe_sl = (pos_type == POSITION_TYPE_BUY) ? NormalizeDouble(open_price + 3.0, digits) : NormalizeDouble(open_price - 3.0, digits);
         bool need_move = (pos_type == POSITION_TYPE_BUY) ? (safe_sl > curr_sl) : (curr_sl <= 0.0 || safe_sl < curr_sl);
         if(need_move)
         {
            if(trade.PositionModify(ticket, safe_sl, tp))
            {
               pos_states[state_idx].rollover_sl_done = true;
               PrintFormat("🛡 [SmartTrader Midnight Rollover Guard] %s #%I64u: SL перенесен на гарантированные +$2.00 (%.2f)! Плавающий профит: $%.2f",
                           symbol, ticket, safe_sl, profit);
               ReportExecution(symbol, "MIDNIGHT_ROLLOVER_GUARD", (pos_type == POSITION_TYPE_BUY ? tick.bid : tick.ask), profit,
                               "Midnight Rollover Guard (+2.00 profit locked at 23:30)", 0, ticket, volume);
            }
         }
      }

      // 1. ЧАСТИЧНОЕ ЗАКРЫТИЕ 50% НА ПРОТИВОПОЛОЖНОЙ ЛИКВИДНОСТИ (TP1)
      // ─────────────────────────────────────────────────────────────
      if(InpUsePartialClose && !pos_states[state_idx].partial_done)
      {
         double min_l  = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
         double step_l = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);

         bool tp1_reached = false;
         if(pos_type == POSITION_TYPE_BUY && tick.bid >= eff_tp1)
            tp1_reached = true;
         else if(pos_type == POSITION_TYPE_SELL && tick.ask <= eff_tp1)
            tp1_reached = true;

         if(tp1_reached && volume > min_l)
         {
            double lot_to_close = volume * (InpPartialClosePct / 100.0);
            if(step_l > 0)
               lot_to_close = MathFloor(lot_to_close / step_l) * step_l;

            if(lot_to_close >= min_l && (volume - lot_to_close) >= min_l)
            {
               if(trade.PositionClosePartial(ticket, lot_to_close))
               {
                  pos_states[state_idx].partial_done = true;
                  double exec_p = (pos_type == POSITION_TYPE_BUY) ? tick.bid : tick.ask;
                  PrintFormat("🎯 [SmartTrader Partial Close] %s #%I64u: закрыто %.2f лота (50%%) на TP1/Opposing Liquidity (%.5f)! Остаток runner: %.2f лота.",
                              symbol, ticket, lot_to_close, exec_p, volume - lot_to_close);
                  ReportExecution(symbol, "PARTIAL_CLOSE", exec_p, profit * 0.5, "Opposing Liquidity Pool (TP1 50% Partial)", 0, ticket, lot_to_close);
                  volume = PositionGetDouble(POSITION_VOLUME);
               }
            }
         }
      }

      // ─────────────────────────────────────────────────────────────
      // 2. СТРУКТУРНЫЙ АВТО-БЕЗУБЫТОК (ПОСЛЕ 1.0R ЗА PROTECTED SWING)
      // ─────────────────────────────────────────────────────────────
      if(InpUseAutoBE)
      {

         if(pos_type == POSITION_TYPE_BUY)
         {
            double current_run = tick.bid - open_price;
            if(current_run >= be_trigger_dist || current_run >= risk_dist || pos_states[state_idx].partial_done)
            {
               double protected_swing = FindProtectedSwingPrice(symbol, POSITION_TYPE_BUY, open_price, pos_time, point, digits);
               double be_floor = NormalizeDouble(open_price + be_offset_dist, digits);
               double target_sl = (protected_swing > be_floor) ? protected_swing : be_floor;
               target_sl = NormalizeDouble(target_sl, digits);

               if(is_gold && is_night_clearing && (tick.bid - target_sl) < 4.0) { /* Night clearing buffer protection */ } else if(target_sl > curr_sl && (tick.bid - target_sl) > min_stop_dist)
               {
                  if(trade.PositionModify(ticket, target_sl, tp))
                  {
                     pos_states[state_idx].be_done = true;
                     PrintFormat("🛡 [SmartTrader Structural BE] BUY %s #%I64u: SL перенесен за Protected Low -> %.5f (+%.1f pips)! Профит: $%.2f",
                                 symbol, ticket, target_sl, (target_sl - open_price) / pip_size, profit);
                     ReportExecution(symbol, "AUTO_BREAKEVEN", tick.bid, profit, 
                                     StringFormat("Structural BE (+%.1f pips locked behind Protected Low %.5f)", (target_sl - open_price) / pip_size, target_sl), 
                                     0, ticket, volume);
                  }
               }
            }
         }
         else if(pos_type == POSITION_TYPE_SELL)
         {
            double current_run = open_price - tick.ask;
            if(current_run >= be_trigger_dist || current_run >= risk_dist || pos_states[state_idx].partial_done)
            {
               double protected_swing = FindProtectedSwingPrice(symbol, POSITION_TYPE_SELL, open_price, pos_time, point, digits);
               double be_ceiling = NormalizeDouble(open_price - be_offset_dist, digits);
               double target_sl = (protected_swing > 0.0 && protected_swing < be_ceiling) ? protected_swing : be_ceiling;
               target_sl = NormalizeDouble(target_sl, digits);

               if(is_gold && is_night_clearing && (target_sl - tick.ask) < 4.0) { /* Night clearing buffer protection */ } else if((curr_sl <= 0.0 || target_sl < curr_sl) && (target_sl - tick.ask) > min_stop_dist)
               {
                  if(trade.PositionModify(ticket, target_sl, tp))
                  {
                     pos_states[state_idx].be_done = true;
                     PrintFormat("🛡 [SmartTrader Structural BE] SELL %s #%I64u: SL перенесен за Protected High -> %.5f (+%.1f pips)! Профит: $%.2f",
                                 symbol, ticket, target_sl, (open_price - target_sl) / pip_size, profit);
                     ReportExecution(symbol, "AUTO_BREAKEVEN", tick.ask, profit, 
                                     StringFormat("Structural BE (+%.1f pips locked behind Protected High %.5f)", (open_price - target_sl) / pip_size, target_sl), 
                                     0, ticket, volume);
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
   double diff = MathAbs(entry_price - sl_price);
   if(diff <= 0) return InpFixedLot;

   // Exact monetary risk calculation across all symbols including Gold
   // Formula: lot = risk_amt / ((diff / tick_size) * tick_val)

   double balance   = AccountInfoDouble(ACCOUNT_BALANCE);
   double risk_pct  = (custom_risk > 0.0) ? custom_risk : InpRiskPercent;
   double risk_amt  = balance * (risk_pct / 100.0);
   double def_lot   = (fallback_lot > 0.0) ? fallback_lot : InpFixedLot;
   
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
   if(lot < min_lot)
      return 0.0; // Risk budget exceeded by minimum lot
   return MathMin(max_lot, lot);
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
void ReportExecution(string symbol, string action, double price, double profit=0.0, string reason="", int signal_id=0, ulong ticket=0, double volume=0.0)
{
   string url = InpServerUrl + "/api/v1/bridge/report";
   string headers = "Content-Type: application/json\r\n";
   headers += "X-API-Key: " + InpApiKey + "\r\n";
   string esc_reason = reason;
   StringReplace(esc_reason, "\"", "'");
   StringReplace(esc_reason, "\r", " ");
   StringReplace(esc_reason, "\n", " ");
   double lot_val = (volume > 0.0) ? volume : InpFixedLot;
   string body = StringFormat("{\"symbol\":\"%s\",\"action\":\"%s\",\"price\":%.5f,\"profit\":%.2f,\"lot\":%.2f,\"reason\":\"%s\",\"signal_id\":%d,\"ticket\":%I64u}", 
                              symbol, action, price, profit, lot_val, esc_reason, signal_id, ticket);
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
                  ReportExecution(sym, "MANUAL_CLOSE", price, profit, "MANUAL_CLIENT_CLOSE", 0, deal_ticket, volume);
               }
               else if(is_tp)
               {
                  Print("🏆 [SmartTrader Bridge] Тейк-профит сработал: ", sym, " #", deal_ticket, " | Профит: ", profit);
                  ReportExecution(sym, "DEAL_CLOSED", price, profit, "TP_HIT", 0, deal_ticket, volume);
               }
               else if(is_sl)
               {
                  if(profit >= -0.20)
                  {
                     Print("🛡 [SmartTrader Bridge] Сделка закрыта по Умному Безубытку (+2 п.): ", sym, " #", deal_ticket, " | Профит: +", profit);
                     ReportExecution(sym, "DEAL_CLOSED", price, profit, "BREAKEVEN_CLOSE", 0, deal_ticket, volume);
                  }
                  else
                  {
                     Print("🛑 [SmartTrader Bridge] Стоп-лосс сработал: ", sym, " #", deal_ticket, " | Убыток: ", profit);
                     ReportExecution(sym, "DEAL_CLOSED", price, profit, "SL_HIT", 0, deal_ticket, volume);
                  }
               }
               else
               {
                  Print("📢 [SmartTrader Bridge] Закрыта сделка ", sym, " #", deal_ticket, " | Профит: ", profit, " USD | Цена: ", price);
                  ReportExecution(sym, "DEAL_CLOSED", price, profit, comment, 0, deal_ticket, volume);
               }
            }
            else if(entry_type == DEAL_ENTRY_IN)
            {
               ulong order_ticket = HistoryDealGetInteger(deal_ticket, DEAL_ORDER);
               ulong pos_ticket = HistoryDealGetInteger(deal_ticket, DEAL_POSITION_ID);
               if(order_ticket > 0 && pos_ticket > 0)
               {
                  int ord_idx = FindPositionState(order_ticket);
                  if(ord_idx >= 0)
                  {
                     RegisterSignalTargets(pos_ticket, pos_states[ord_idx].tp1, pos_states[ord_idx].tp2, pos_states[ord_idx].initial_sl);
                  }
               }
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
               ReportExecution(sym, "MANUAL_CLOSE", price, profit, "MANUAL_CLIENT_CLOSE", 0, deal_ticket, volume);
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


