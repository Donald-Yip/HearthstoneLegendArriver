from time import sleep
from FSM_action import system_exit, AutoHS_automata
import keyboard
from log_state import check_name
from print_info import print_info_init
from FSM_action import init

import constants.constants


if __name__ == "__main__":
    print("请设置确认当前系统分辨率为：1920-1080")
    print("请设置确认当前系统缩放为：100%")
    print("请设置确认炉石传说设置为：全屏")
    print("请保持炉石传说与炉石盒子可见，程序将自动读取左侧 AI 打法并执行")
    print("按 Ctrl+Q 可随时停止自动化并退出程序")
    
    MY_NAME = constants.constants.YOUR_NAME
    HEARTHSTONE_LOG_ROOT = constants.constants.HEARTHSTONE_LOG_ROOT
    #input("请确认你的用户名为："+MY_NAME+"。炉石日志目录为："+HEARTHSTONE_LOG_ROOT+"（确认完成后请回车）")
    print("你好"+MY_NAME)
    sleep(2)
    # check_name()
    print_info_init()
    init()
    keyboard.add_hotkey("ctrl+q", system_exit)
    try:
        AutoHS_automata()
    except KeyboardInterrupt:
        # Ctrl+Q interrupts the main thread even while it is blocked in input().
        pass

