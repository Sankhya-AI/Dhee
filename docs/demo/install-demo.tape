Output docs/demo/install-demo.gif
Output docs/demo/install-demo.mp4

Require curl
Require sh

Set Shell "zsh"
Set FontSize 15
Set Width 1120
Set Height 680
Set Theme '{"name":"Dhee Calm","black":"#000000","red":"#ff5f5f","green":"#e8e8e8","yellow":"#ff9f1c","blue":"#9aa7ff","purple":"#d6d6d6","cyan":"#d6d6d6","white":"#e8e8e8","brightBlack":"#6a6a6a","brightRed":"#ff7777","brightGreen":"#ffffff","brightYellow":"#ffb347","brightBlue":"#b8c0ff","brightPurple":"#ffffff","brightCyan":"#ffffff","brightWhite":"#ffffff","background":"#000000","foreground":"#e8e8e8","selectionBackground":"#303030","cursorColor":"#e8e8e8"}'
Set Padding 20
Set Framerate 12
Set TypingSpeed 8ms
Set PlaybackSpeed 1.6
Set WindowBar "Rings"
Set BorderRadius 8

Hide
Type "cd /Users/chitranjanmalviya/Desktop/Dhee" Enter
Type "export DEMO_HOME=/tmp/dhee-install-demo-home DEMO_WORKSPACE=/tmp/dhee-install-demo-workspace" Enter
Type "export DEMO_PATH=/usr/bin:/bin:/opt/homebrew/bin:/usr/local/bin" Enter
Type "export HOME=$DEMO_HOME SHELL=/bin/zsh PATH=$DEMO_HOME/.local/bin:$DEMO_PATH" Enter
Type "export DHEE_INSTALL_PACKAGE=$PWD/dist/dhee-7.1.0-py3-none-any.whl" Enter
Type "rm -rf $DEMO_HOME $DEMO_WORKSPACE && mkdir -p $DEMO_WORKSPACE" Enter
Type "clear" Enter
Show

Type "curl -fsSL https://raw.githubusercontent.com/Sankhya-AI/Dhee/main/install.sh | sh" Enter
Sleep 46s
Enter
Sleep 2s
Type "dhee-demo-key" Enter
Sleep 8s

Type "cd $DEMO_WORKSPACE" Enter
Type "dhee init --skip-ingest --skip-first-light" Enter
Sleep 5s

Type "dhee status" Enter
Sleep 4s

Type "dhee completion --shell zsh | sed -n '1,9p'" Enter
Sleep 5s
