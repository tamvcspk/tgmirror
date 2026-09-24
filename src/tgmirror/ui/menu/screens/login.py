""" "Đăng nhập": what ``tgmirror login`` asks (api_id/api_hash when missing, phone, code, 2FA
password), drawn in the frame, with the same ``core.auth.login`` flow underneath.

Esc leaves the login at any question (a login is not a list of steps to go back through: every
answer is sent to Telegram at once). The code, the password and the api_hash are masked while
typed and the phone number is never shown again once entered (CLAUDE.md rule 6).
"""

from tgmirror.cli.commands.auth import LoginPromptsOn, ensure_credentials, who
from tgmirror.core.auth import login
from tgmirror.ui.menu.context import AppContext
from tgmirror.ui.menu.prompter import MenuPrompter
from tgmirror.ui.menu.screen import ScreenResult
from tgmirror.ui.menu.screens.info import InfoScreen
from tgmirror.ui.menu.screens.wizard import WizardScreen
from tgmirror.ui.messages import t


def login_screen(app: AppContext) -> WizardScreen:
    async def flow(prompter: MenuPrompter) -> ScreenResult:
        config = await ensure_credentials(
            app.rt, prompter=prompter, interactive=True, echo=prompter.say
        )
        conn = app.conn or await app.open_connection(config)
        account = await conn.auth.account()
        if account is None:
            prompts = LoginPromptsOn(prompter, interactive=True, echo=prompter.say)
            account = await login(conn.auth, prompts)
        app.account = account
        return ("replace", InfoScreen([t("login.ok", who=who(account))]))

    return WizardScreen(t("menu.item_login"), flow, prompter=MenuPrompter(private_text=True))
