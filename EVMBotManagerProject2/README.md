### EVM Wallet Bot

* A Telegram bot to manage, monitor, and analyze your multi-chain EVM wallets.
* This bot allows you to add wallets (Ethereum, BSC, Base, Arbitrum), track real-time balances, visualize portfolio distribution via charts, all within Telegram.



##### Features

###### Wallet Management:


###### Balances:


###### Network Comparison:


###### Private Balance Mode:


###### Daily Summary:


##### Prerequisites

Before installing, ensure the following installed on the system:

* **Python: 3.9 or later**
* **pip: Latest**
* **PyCharm: Community or Professional**
* **Telegram: Bot Token From @BotFather**

 



##### Step-by-Step Installation Guide (Using PyCharm)

**Open Project in PyCharm**

* Open PyCharm
* Click “Open” → Select the project folder
* Wait for indexing to complete



**Configure Python Interpreter**

* Go to File → Settings → Project → Python Interpreter
* Click Add Interpreter



**Select**

* Add New Environment → Virtualenv
* Base Interpreter: select your Python 3.9+ installation
* Click OK to create the virtual environment



**Install Dependencies**

Open the PyCharm Terminal (bottom panel) and run:

* pip install python-telegram-bot==20.7
* pip install web3
* pip install matplotlib
* pip install requests





**Set Up Telegram Bot Token**

* Open Telegram and search for @BotFather
* Run the command /newbot
* Follow the prompts and copy your Bot Token
* Open the project file and locate this line:
* TOKEN = "YOUR\_BOT\_TOKEN\_HERE"
* Replace the placeholder with the actual bot token.





###### Run the Bot

**In PyCharm, open main.py, then:**

Click Run




**Open Telegram and search your bot by its username (@WalletBOTproject_bot)**



Click Start



**The bot menu and available commands**



Usage Guide

Available Commands

Command	Description

/start	Display main menu and help

/addwallet	Add a new wallet interactively

/listwallets	View all saved wallets with balances

/removewallet	Remove an existing wallet

/summary	Show portfolio summary \& USD totals

/hideon	Hide balances (privacy mode)

/hideoff	Show balances again
