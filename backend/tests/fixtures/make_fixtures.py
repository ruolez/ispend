"""Generate synthetic statement fixtures. Run once: python tests/fixtures/make_fixtures.py"""
import json
import os
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))

CSV = {
    "chase_checking.csv": """Details,Posting Date,Description,Amount,Type,Balance,Check or Slip #
DEBIT,08/01/2024,"STARBUCKS STORE 12345 CHICAGO IL",-6.45,DEBIT_CARD,1234.56,
CREDIT,08/02/2024,"PAYROLL ACME CORP DIRECT DEP",2500.00,ACH_CREDIT,3734.56,
DEBIT,08/03/2024,"Online Payment 1234567 To Chase Card",-450.00,ACCT_XFER,3284.56,
DEBIT,08/05/2024,"WHOLEFDS LKV 10231 CHICAGO IL",-84.12,DEBIT_CARD,3200.44,
DEBIT,08/05/2024,"WHOLEFDS LKV 10231 CHICAGO IL",-84.12,DEBIT_CARD,3116.32,
CHECK,08/07/2024,"CHECK 1042",-120.00,CHECK_PAID,2996.32,1042
DEBIT,08/09/2024,"COMED ONLINE PMT",-95.30,ACH_DEBIT,2901.02,
DEBIT,08/12/2024,"ATM WITHDRAWAL 00012345 CHICAGO IL",-100.00,ATM,2801.02,
""",
    "chase_card.csv": """Transaction Date,Post Date,Description,Category,Type,Amount,Memo
08/01/2024,08/02/2024,AMZN Mktp US*2A3BC4D5E,Shopping,Sale,-42.99,
08/03/2024,08/04/2024,SQ *BLUE BOTTLE COFFEE,Food & Drink,Sale,-5.75,
08/04/2024,08/05/2024,Payment Thank You-Mobile,,Payment,450.00,
08/06/2024,08/07/2024,UBER *EATS,Food & Drink,Sale,-28.40,
08/10/2024,08/11/2024,NETFLIX.COM,Bills & Utilities,Sale,-15.49,
08/15/2024,08/16/2024,SHELL OIL 12345678 EVANSTON IL,Gas,Sale,-52.10,
""",
    "amex.csv": """Date,Description,Card Member,Account #,Amount
08/02/2024,WHOLE FOODS MARKET CHICAGO IL,JOHN DOE,-11002,84.12
08/04/2024,DELTA AIR LINES ATLANTA GA,JOHN DOE,-11002,412.30
08/06/2024,AUTOPAY PAYMENT - THANK YOU,JOHN DOE,-11002,-600.00
08/09/2024,SPOTIFY USA,JOHN DOE,-11002,10.99
08/12/2024,TST* THE BURGER JOINT EVANSTON IL,JOHN DOE,-11002,23.80
""",
    "capital_one_card.csv": """Transaction Date,Posted Date,Card No.,Description,Category,Debit,Credit
2024-08-01,2024-08-02,1234,TARGET 00012345 CHICAGO IL,Merchandise,63.20,
2024-08-03,2024-08-04,1234,CAPITAL ONE AUTOPAY PYMT,Payment/Credit,,300.00
2024-08-05,2024-08-06,1234,CHIPOTLE 1234 CHICAGO IL,Dining,12.85,
2024-08-08,2024-08-09,1234,LYFT *RIDE SAT 9PM,Other Travel,18.40,
""",
    "capital_one_360.csv": """Account Number,Transaction Date,Transaction Amount,Transaction Type,Transaction Description,Balance
1234,08/01/24,75.00,Debit,Withdrawal to Chase,925.00
1234,08/05/24,1500.00,Credit,Deposit from ACME PAYROLL,2425.00
1234,08/09/24,45.50,Debit,Debit Card Purchase - TRADER JOE'S #123,2379.50
""",
    "bofa_checking.csv": """Description,,Summary Amt.
Beginning balance as of 08/01/2024,,"1,000.00"
Total credits,,"2,500.00"
Total debits,,"-812.34"
Ending balance as of 08/31/2024,,"2,687.66"

Date,Description,Amount,Running Bal.
08/01/2024,Beginning balance as of 08/01/2024,,"1,000.00"
08/02/2024,"ACME CORP DES:PAYROLL ID:123456 INDN:JOHN DOE CO ID:1234567890 PPD","2,500.00","3,500.00"
08/05/2024,"CHECKCARD 0803 KROGER #123 CHICAGO IL 24692164215100012345678",-92.34,"3,407.66"
08/10/2024,"Zelle payment to Jane Doe Conf# abc123def",-200.00,"3,207.66"
08/15/2024,"COMED DES:UTIL BILL ID:XXXXX1234 INDN:JOHN DOE CO ID:XXXXX PPD",-120.00,"3,087.66"
08/20/2024,"ATM WITHDRAWAL 08/20 0001234 CHICAGO IL",-400.00,"2,687.66"
""",
    "bofa_card.csv": """Posted Date,Reference Number,Payee,Address,Amount
08/03/2024,24692164215100012345678,STARBUCKS STORE 12345,"CHICAGO IL",-6.45
08/05/2024,24692164215100012345679,PAYMENT - THANK YOU,,350.00
08/09/2024,24692164215100012345680,WALGREENS #1234,"CHICAGO IL",-24.10
""",
    "citi.csv": """Status,Date,Description,Debit,Credit
Cleared,08/02/2024,COSTCO WHSE #1234 CHICAGO IL,156.78,
Cleared,08/04/2024,AUTOPAY PAYMENT THANK YOU,,-500.00
Cleared,08/07/2024,APPLE.COM/BILL 866-712-7753 CA,9.99,
Cleared,08/11/2024,REFUND COSTCO WHSE #1234,,-20.00
""",
    "discover.csv": """Trans. Date,Post Date,Description,Amount,Category
08/01/2024,08/02/2024,WALMART SUPERCENTER CHICAGO IL,88.20,Supermarkets
08/05/2024,08/06/2024,INTERNET PAYMENT - THANK YOU,-200.00,Payments and Credits
08/09/2024,08/10/2024,DUNKIN #123456 CHICAGO IL,4.35,Restaurants
""",
    "wells_fargo.csv": """"08/01/2024","-45.20","*","","PURCHASE AUTHORIZED ON 07/31 TRADER JOE'S #123 CHICAGO IL S1234567890 CARD 1234"
"08/03/2024","2500.00","*","","ACME CORP PAYROLL 240803 JOHN DOE"
"08/06/2024","-12.00","*","","MONTHLY SERVICE FEE"
"08/09/2024","-64.30","*","","PURCHASE AUTHORIZED ON 08/08 SHELL OIL 12345 EVANSTON IL P1234 CARD 1234"
"08/15/2024","-1200.00","*","","ONLINE TRANSFER TO SAVINGS REF #IB0ABC123"
""",
    "rbc.csv": """"Account Type","Account Number","Transaction Date","Cheque Number","Description 1","Description 2","CAD$","USD$"
Chequing,01234-5678901,8/1/2024,,"TIM HORTONS #4412","TORONTO ON",-4.25,
Chequing,01234-5678901,8/2/2024,,"PAYROLL DEPOSIT","ACME LTD",3200.00,
Chequing,01234-5678901,8/5/2024,,"LOBLAWS #1234","TORONTO ON",-96.40,
Chequing,01234-5678901,8/7/2024,,"INTERAC E-TRANSFER SENT","JOHN SMITH",-150.00,
Chequing,01234-5678901,8/9/2024,,"BELL CANADA","PAP",-85.00,
""",
    "td.csv": """08/01/2024,TIM HORTONS #4412,4.25,,1500.00
08/02/2024,PAYROLL DEPOSIT ACME,,3200.00,4700.00
08/05/2024,SOBEYS #123 HALIFAX,96.40,,4603.60
08/07/2024,SEND E-TFR ***abc,150.00,,4453.60
""",
    "bmo_chequing.csv": """Following data is valid as of 20240831
First Bank Card,Transaction Type,Date Posted,Transaction Amount,Description
'1234567890,DEBIT,20240801,-52.30,SHOPPERS DRUG MART #123 TORONTO ON
'1234567890,CREDIT,20240802,3100.00,PAYROLL ACME LTD
'1234567890,DEBIT,20240806,-14.10,STARBUCKS #4455 TORONTO ON
""",
    "bmo_card.csv": """Item #,Card #,Transaction Date,Posting Date,Transaction Amount,Description
1,'5191XXXXXXXX1234,20240801,20240802,45.60,CANADIAN TIRE #123 TORONTO ON
2,'5191XXXXXXXX1234,20240803,20240804,-500.00,PAYMENT RECEIVED - THANK YOU
3,'5191XXXXXXXX1234,20240806,20240807,12.99,SPOTIFY
""",
    "scotiabank.csv": """"08/01/2024","-4.25","*","","TIM HORTONS #4412 HALIFAX NS"
"08/02/2024","3200.00","*","","PAYROLL DEPOSIT ACME"
"08/05/2024","-96.40","*","","SOBEYS #123 HALIFAX NS"
""",
    "pnc_checking.csv": """Date,Description,Withdrawals,Deposits,Category,Balance
"08/12/2024","ATM WITHDRAWAL          PNC BANK ATM 5TH AVE        PITTSBURGH  PA","$100.00","","ATM/Cash","$2,801.02"
"08/09/2024","DEBIT CARD PURCHASE   XXXXX4291 GIANT EAGLE #0012       PITTSBURGH  PA","$84.12","","Groceries","$2,901.02"
"08/05/2024","ONLINE TRANSFER TO SAVINGS XXXXXX1234","$450.00","","Transfers","$2,985.14"
"08/02/2024","DIRECT DEPOSIT        ACME CORP PAYROLL","","$2,500.00","Paychecks","$3,435.14"
"08/01/2024","RECURRING DEBIT CARD  XXXXX3815 NETFLIX.COM               NETFLIX.COM CA","$15.49","","Entertainment","$935.14"
""",
    "pnc_classic.csv": """Date,Description,Withdrawals,Deposits,Balance
08/12/2024,ATM WITHDRAWAL PNC BANK ATM 5TH AVE PITTSBURGH PA,100.00,,2801.02
08/09/2024,DEBIT CARD PURCHASE XXXXX4291 GIANT EAGLE #0012 PITTSBURGH PA,84.12,,2901.02
08/02/2024,DIRECT DEPOSIT ACME CORP PAYROLL,,2500.00,2985.14
""",
    "pnc_activity.csv": """Transaction Date,Transaction Description,Amount,Balance
2024-08-12,ATM WITHDRAWAL PNC BANK ATM 5TH AVE PITTSBURGH PA,-$100.00,$2801.02
2024-08-09,DEBIT CARD PURCHASE XXXXX4291 GIANT EAGLE #0012 PITTSBURGH PA,-$84.12,$2901.02
2024-08-02,DIRECT DEPOSIT ACME CORP PAYROLL,$2500.00,$2985.14
""",
    "generic_headerless.csv": """2024-08-01,Coffee shop,-4.50,995.50
2024-08-02,Salary,3000.00,3995.50
2024-08-03,Grocery store,-120.00,3875.50
""",
    "generic_semicolon.csv": """Date;Payee;Withdrawal;Deposit;Balance
13/08/2024;COFFEE SHOP;4.50;;995.50
14/08/2024;SALARY;;3000.00;3995.50
15/08/2024;GROCERY STORE;120.00;;3875.50
""",
    "generic_positive_charges.csv": """Date,Description,Amount
08/01/2024,COFFEE SHOP,4.50
08/02/2024,GROCERY STORE,120.00
08/03/2024,GAS STATION,52.10
08/04/2024,RESTAURANT,38.00
08/05/2024,PHARMACY,12.30
08/06/2024,PAYMENT THANK YOU,-200.00
""",
}


def words_from_lines(lines, start_top=100.0, line_h=14.0, char_w=6.0):
    """lines: list of [(segment_text, x0), ...]; returns pdfplumber-like word dicts."""
    words = []
    for i, segments in enumerate(lines):
        top = start_top + i * line_h
        for text, x in segments:
            cursor = x
            for w in text.split():
                width = char_w * len(w)
                words.append({"text": w, "x0": cursor, "x1": cursor + width, "top": top, "bottom": top + 10})
                cursor += width + char_w * 0.6
    return words


PDF_WORDS = {
    "words_chase_card.json": [
        [("JPMorgan Chase Bank, N.A.", 30)],
        [("Opening/Closing Date 07/15/24 - 08/14/24", 30)],
        [("ACCOUNT ACTIVITY", 30)],
        [("Date of Transaction", 30), ("Merchant Name or Transaction Description", 60), ("$ Amount", 500)],
        [("PAYMENTS AND OTHER CREDITS", 30)],
        [("07/20", 30), ("Payment Thank You-Mobile", 60), ("-450.00", 520)],
        [("PURCHASE", 30)],
        [("07/22", 30), ("AMZN Mktp US*2A3BC4D5E Amzn.com/bill WA", 60), ("42.99", 520)],
        [("07/25", 30), ("SQ *BLUE BOTTLE COFFEE CHICAGO IL", 60), ("5.75", 520)],
        [("07/28", 30), ("UBER *EATS SAN FRANCISCO CA", 60), ("28.40", 520)],
        [("help.uber.com CA", 60)],
        [("08/10", 30), ("NETFLIX.COM LOS GATOS CA", 60), ("15.49", 520)],
        [("TOTAL INTEREST CHARGED IN 2024", 30), ("0.00", 520)],
        [("08/12", 30), ("SHOULD NOT PARSE AFTER STOP", 60), ("99.99", 520)],
    ],
    "words_amex.json": [
        [("American Express", 30)],
        [("Closing Date 08/14/24", 30)],
        [("New Charges", 30)],
        [("08/02/24*", 30), ("WHOLE FOODS MARKET CHICAGO IL", 90), ("$84.12", 520)],
        [("08/09/24", 30), ("SPOTIFY USA NEW YORK NY", 90), ("$10.99", 520)],
        [("Payments and Credits", 30)],
        [("08/06/24", 30), ("AUTOPAY PAYMENT - THANK YOU", 90), ("-$600.00", 520)],
        [("Total Fees", 30), ("$0.00", 520)],
    ],
    "words_rbc.json": [
        [("RBC Royal Bank", 30)],
        [("Your account statement", 30)],
        [("From August 1, 2024 to August 31, 2024", 30)],
        [("Date", 30), ("Description", 80), ("Withdrawals ($)", 330), ("Deposits ($)", 420), ("Balance ($)", 510)],
        [("Opening Balance", 80), ("1,000.00", 520)],
        [("Aug 1", 30), ("TIM HORTONS #4412", 80), ("4.25", 350), ("995.75", 520)],
        [("Aug 2", 30), ("PAYROLL DEPOSIT ACME LTD", 80), ("3,200.00", 430), ("4,195.75", 520)],
        [("Aug 5", 30), ("LOBLAWS #1234", 80), ("96.40", 350), ("4,099.35", 520)],
        [("Closing Balance", 80), ("4,099.35", 520)],
    ],
    "words_generic.json": [
        [("Some Credit Union", 30)],
        [("Statement Period 08/01/2024 - 08/31/2024", 30)],
        [("08/02/2024", 30), ("COFFEE SHOP", 100), ("-4.50", 420), ("995.50", 520)],
        [("08/03/2024", 30), ("SALARY DEPOSIT", 100), ("3,000.00", 420), ("3,995.50", 520)],
        [("08/05/2024", 30), ("GROCERY STORE", 100), ("-120.00", 420), ("3,875.50", 520)],
    ],
}


def main():
    for name, content in CSV.items():
        with open(os.path.join(HERE, name), "w", newline="") as f:
            f.write(content)
    for name, lines in PDF_WORDS.items():
        with open(os.path.join(HERE, name), "w") as f:
            json.dump(words_from_lines(lines), f, indent=0)
    rows = [("Date", "Description", "Amount"),
            (datetime(2024, 8, 1), "COFFEE SHOP", -4.5),
            (datetime(2024, 8, 2), "SALARY", 3000.0),
            (datetime(2024, 8, 3), "GROCERY STORE", -120.0)]
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Transactions"
    for r in rows:
        ws.append(list(r))
    wb.save(os.path.join(HERE, "generic.xlsx"))
    try:
        import xlwt
    except ImportError:
        print("xlwt missing; skipped generic.xls")
        return
    book = xlwt.Workbook()
    sheet = book.add_sheet("Transactions")
    date_style = xlwt.easyxf(num_format_str="MM/DD/YYYY")
    for i, r in enumerate(rows):
        for j, v in enumerate(r):
            if isinstance(v, datetime):
                sheet.write(i, j, v, date_style)
            else:
                sheet.write(i, j, v)
    book.save(os.path.join(HERE, "generic.xls"))


if __name__ == "__main__":
    main()
