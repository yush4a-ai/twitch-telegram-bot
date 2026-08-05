Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "c:\Users\yusha\Desktop\cloude\twitch-bot"
WshShell.Run "cmd /c """"c:\Users\yusha\Desktop\cloude\twitch-bot\.venv\Scripts\dobriybot.exe"" main.py >> ""c:\Users\yusha\Desktop\cloude\twitch-bot\bot_log.txt"" 2>&1""", 0, False
