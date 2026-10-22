# MDRAP Charm VHS Demo Recording Script
# Output: tools/demo/mdrap_demo.gif
# To run: vhs tools/demo/demo.tape

Output tools/demo/mdrap_demo.gif

Set Shell "bash"
Set FontSize 18
Set Width 1920
Set Height 1080
Set Padding 40
Set Theme "Catppuccin Mocha"
Set WindowBar Colorful

Type "# Scene 1: The Hook — Catching bad market data before it hits trading strategies"
Enter
Sleep 1s
Type "python cli.py bbo BTC/USD"
Enter
Sleep 5s

Type "# Scene 2: Platform diagnostics & environment verification"
Enter
Sleep 1s
Type "python cli.py doctor"
Enter
Sleep 7s

Type "# Scene 3: Multi-venue live pipeline & quality classification"
Enter
Sleep 1s
Type "python cli.py live BTC/USD -l 5 --sim"
Enter
Sleep 14s

Type "# Scene 4: Catching bad data & quarantine inspection"
Enter
Sleep 1s
Type "python cli.py run -e 1000 -s 42 --db data/demo.db"
Enter
Sleep 5s
Type "python cli.py query quarantine 3 --db data/demo.db"
Enter
Sleep 8s

Type "# Scene 5: Consolidated multi-venue Level-2 depth"
Enter
Sleep 1s
Type "python cli.py depth BTC/USD"
Enter
Sleep 9s

Type "# Scene 6: Speed, honestly — Native C core wire-to-SHM"
Enter
Sleep 1s
Type "python cli.py core -e 1000000"
Enter
Sleep 10s

Type "# Scene 7: Cryptographic Merkle audit proof & tamper detection"
Enter
Sleep 1s
Type "export MDRAP_DEMO=1"
Enter
Type "python cli.py audit --verify --db data/demo.db"
Enter
Sleep 4s
Type "python -c \"import sqlite3; c=sqlite3.connect('data/demo.db'); c.execute('UPDATE audit_log SET details=\\\"hacked\\\" WHERE rowid=1'); c.commit()\""
Enter
Type "python cli.py audit --verify --db data/demo.db"
Enter
Sleep 6s

Type "# Scene 8: Get started in 5 minutes"
Enter
Sleep 1s
Type "echo 'MDRAP: https://github.com/Aryan-20-04/mdrap | pip install mdrap'"
Enter
Sleep 6s
