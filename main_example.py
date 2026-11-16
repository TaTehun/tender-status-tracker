import os
import sys
import ctypes
import time
import logging
from datetime import date
from dotenv import load_dotenv
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait, Select
import selenium.webdriver.support.expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

# email_pipeline_example.py bundles email_setup.py + sender.py + log_utils.py
# into one reference file. Split it into those three for production use.
import email_pipeline_example as cfg
from email_pipeline_example import load_sender, setup_logger, notify_failure
from utils.email_utils import build_tender_email_body
from utils.date_utils import today_str

log, _log_file = setup_logger(cfg.LOG_BASE, 'main')

CARRIER_IDS = 'CARRIER_A,CARRIER_B'
CARRIER_IDS_PROVIDER_B = 'CARRIER_C'
TENDER_STATUS_VALUES = [
    'STATUS_1',
    'STATUS_2',
    'STATUS_3',
]

RUNS = {
    'region_a': {
        'logistics_groups': ['GRP1', 'GRP2', 'GRP3'],
        'label': 'Region A',
        'region': 'RegionA',
        'email_cc': ['region-a-cc@example.com'],
    },
    'region_b': {
        'logistics_groups': ['GRP4', 'GRP5'],
        'label': 'Region B',
        'region': 'RegionB',
        'email_cc': ['region-b-cc@example.com'],
    },
    'region_c': {
        'logistics_groups': ['GRP6'],
        'label': 'Region C',
        'region': 'RegionC',
        'email_cc': ['region-c-cc@example.com'],
    },
}


class TenderCrawl:
    def __init__(self, username, password, download_path):
        option = webdriver.ChromeOptions()
        prefs = {
            "download.default_directory": str(download_path),
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "safebrowsing.enabled": True,
        }
        option.add_experimental_option("prefs", prefs)
        service = Service(ChromeDriverManager().install())
        self.driver = webdriver.Chrome(service=service, options=option)
        self.driver.execute_cdp_cmd('Browser.setDownloadBehavior', {
            'behavior': 'allow',
            'downloadPath': str(download_path),
        })

        winapi = ctypes.windll.user32
        width = winapi.GetSystemMetrics(0)
        height = winapi.GetSystemMetrics(1)
        self.driver.set_window_size(width, height)
        self.driver.maximize_window()

        self.driver.get('https://your-tms-url/framework/Frame.jsp')

        WebDriverWait(self.driver, 10).until(
            EC.presence_of_element_located((By.NAME, 'i2ui_shell_content')))
        self.driver.switch_to.frame(self.driver.find_element('name', 'i2ui_shell_content'))
        self.driver.switch_to.frame(self.driver.find_element('id', 'resultFrame'))
        self.driver.find_element('name', 'loginUser').send_keys(username)
        self.driver.find_element('id', 'dspLoginPassword').send_keys(password)
        WebDriverWait(self.driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, '.loginInner'))).click()

        WebDriverWait(self.driver, 30).until(
            EC.invisibility_of_element_located((By.NAME, 'loginUser')))
        log.info("Login complete")

    def switch_to_nav(self):
        self.driver.switch_to.default_content()
        WebDriverWait(self.driver, 10).until(
            EC.presence_of_element_located((By.NAME, 'i2ui_shell_content')))
        self.driver.switch_to.frame(self.driver.find_element('name', 'i2ui_shell_content'))
        self.driver.switch_to.frame(self.driver.find_element('id', 'navFrame'))

    def switch_to_result(self):
        self.driver.switch_to.default_content()
        WebDriverWait(self.driver, 10).until(
            EC.presence_of_element_located((By.NAME, 'i2ui_shell_content')))
        self.driver.switch_to.frame(self.driver.find_element('name', 'i2ui_shell_content'))
        self.driver.switch_to.frame(self.driver.find_element('id', 'resultFrame'))

    def navigate_to_tender_request(self):
        self.switch_to_nav()
        time.sleep(1)

        # Nav tree branch may already be expanded from a prior run in the same session
        loads_link = self.driver.find_elements(By.XPATH, '//*[@id="TREECELL_navigation_8_1"]/a[2]')
        if not (loads_link and loads_link[0].is_displayed()):
            log.info("Tender Requests branch not visible, expanding")
            WebDriverWait(self.driver, 60).until(
                EC.element_to_be_clickable((By.ID, 'TREECELLIMAGE_navigation_navigation_8'))).click()
            time.sleep(1)
            loads_link = [WebDriverWait(self.driver, 60).until(
                EC.element_to_be_clickable((By.XPATH, '//*[@id="TREECELL_navigation_8_1"]/a[2]')))]

        loads_link[0].click()
        log.info("Navigated to Tender Requests")

    def set_search_conditions(self, logistics_groups: list, carrier_id: str = CARRIER_IDS):
        self.switch_to_result()

        log.info("Selecting System Default saved search")
        WebDriverWait(self.driver, 60).until(
            EC.presence_of_element_located((By.NAME, 'savedSearches')))
        Select(self.driver.find_element(By.NAME, 'savedSearches')).select_by_visible_text('System Default')

        self.driver.find_element(By.ID, 'principalID').clear()

        self.switch_to_result()
        time.sleep(5)

        log.info("Clicking Search button")
        WebDriverWait(self.driver, 60).until(
            EC.element_to_be_clickable((By.XPATH, '//img[@alt="Search"]'))).click()

        WebDriverWait(self.driver, 60).until(
            EC.presence_of_element_located((By.NAME, 'carrierID')))

        # status_ is a hidden select, set via JS + site's own showSelected() callback
        log.info("Selecting tender status values")
        select_elem = self.driver.find_element(By.NAME, 'status_')
        self.driver.execute_script("""
            var select = arguments[0];
            var values = arguments[1];
            for (var i = 0; i < select.options.length; i++) {
                select.options[i].selected = values.indexOf(select.options[i].value) >= 0;
            }
            status_tmcombobox.showSelected();
        """, select_elem, TENDER_STATUS_VALUES)

        # logisticsGroup_ is also a hidden select, same JS injection pattern
        log.info("Selecting logistics groups")
        select_elem = self.driver.find_element(By.NAME, 'logisticsGroup_')
        self.driver.execute_script("""
            var select = arguments[0];
            var values = arguments[1];
            for (var i = 0; i < select.options.length; i++) {
                select.options[i].selected = values.indexOf(select.options[i].value) >= 0;
            }
            logisticsGroup_tmcombobox.showSelected();
        """, select_elem, logistics_groups)

        log.info(f"Entering carrier ID: {carrier_id}")
        carrier = self.driver.find_element(By.NAME, 'carrierID')
        carrier.clear()
        carrier.send_keys(carrier_id)

        log.info("Expanding date section")
        expand_btn = self.driver.find_element(By.CSS_SELECTOR, '#loadDatesSearchSection img[src*="container_expand"]')
        self.driver.execute_script("arguments[0].click();", expand_btn)

        log.info("Setting date range to TODAY")
        WebDriverWait(self.driver, 60).until(
            EC.presence_of_element_located((By.NAME, 'tenderResponseRequiredFromRelativeDateKeyword')))
        Select(self.driver.find_element(By.NAME, 'tenderResponseRequiredFromRelativeDateKeyword')).select_by_value('TODAY')
        Select(self.driver.find_element(By.NAME, 'tenderResponseRequiredToRelativeDateKeyword')).select_by_value('TODAY')

        log.info("Submitting search")
        WebDriverWait(self.driver, 60).until(
            EC.element_to_be_clickable((By.XPATH, '//a[contains(@href,"submitSearch")]'))).click()
        log.info(f"Search submitted, carrier: {carrier_id}")
        time.sleep(5)

    def is_no_data(self) -> bool:
        self.switch_to_result()
        try:
            no_records = bool(self.driver.find_elements(By.XPATH, '//*[contains(text(),"There are no records found")]'))
            export_btn = bool(self.driver.find_elements(By.XPATH, '//img[@alt="Export Data"]'))
            if no_records and not export_btn:
                log.warning("No data: 'no records found' message detected and Export button absent")
                return True
        except Exception:
            pass
        return False

    def export_results(self, download_dir: Path, region: str, carrier_label: str = 'ProviderA') -> Path:
        self.switch_to_result()

        log.info("Clicking Export Data")
        WebDriverWait(self.driver, 120).until(
            EC.element_to_be_clickable((By.XPATH, '//img[@alt="Export Data"]'))).click()

        log.info("Checking All Rows")
        WebDriverWait(self.driver, 120).until(
            EC.presence_of_element_located((By.NAME, 'allRows')))
        self.driver.find_element(By.NAME, 'allRows').click()
        time.sleep(10)

        # Submit button is in a popup window
        log.info("Clicking Submit")
        handles = self.driver.window_handles
        if len(handles) > 1:
            self.driver.switch_to.window(handles[-1])
        WebDriverWait(self.driver, 120).until(
            EC.element_to_be_clickable((By.ID, 'buttonEmphasized'))).click()

        # Poll for downloaded file (up to 60s)
        end_time = time.time() + 60
        file = None
        while time.time() < end_time:
            for f in download_dir.iterdir():
                if f.suffix == '.xlsx' and 'TM_ExportData' in f.name:
                    file = f
                    break
            if file:
                break
            time.sleep(1)

        # Close popup
        handles = self.driver.window_handles
        if len(handles) > 1:
            self.driver.switch_to.window(handles[-1])
            self.driver.close()
            self.driver.switch_to.window(handles[0])

        # Click Cancel to return to main page
        self.switch_to_result()
        WebDriverWait(self.driver, 30).until(
            EC.element_to_be_clickable((By.ID, 'buttonRegular'))).click()

        if not file:
            log.warning("No download file found")
            return None

        today = date.today().strftime('%m%d%Y')
        save_path = download_dir / f'{carrier_label}_Report_{region}_{today}.xlsx'
        if save_path.exists():
            save_path.unlink()
        file.rename(save_path)
        log.info(f"Export complete: {save_path.name}")
        return save_path

    def quit(self):
        self.driver.quit()


if __name__ == '__main__':
    from email_pipeline_example import _flush_to_file

    def _exit(msg: str, code: int = 1):
        log.error(msg)
        _flush_to_file(_log_file)
        sys.exit(code)

    if len(sys.argv) < 2 or sys.argv[1] not in RUNS:
        _exit(f"Usage: python main_example.py [{'|'.join(RUNS.keys())}]")

    run = RUNS[sys.argv[1]]
    log.info(f"Run: {run['label']}")

    load_dotenv(cfg.ENV_PATH)
    username = os.getenv(cfg.TMS_USER_ENV)
    password = os.getenv(cfg.TMS_PASS_ENV)
    if not username or not password:
        _exit("TMS credentials missing, check .env file")

    download_dir = cfg.DOWNLOAD_BASE
    download_dir.mkdir(parents=True, exist_ok=True)

    # Remove oldest files if at or above the limit, per carrier
    for pattern in ('ProviderA_Report_*.xlsx', 'ProviderB_Report_*.xlsx'):
        existing = sorted(download_dir.glob(pattern), key=lambda f: f.stat().st_mtime)
        for old_file in existing[:-cfg.MAX_DATA_FOLDERS + 1] if len(existing) >= cfg.MAX_DATA_FOLDERS else []:
            old_file.unlink()
            log.info(f"Deleted old file: {old_file.name}")

    def run_export(crawl, carrier_id, carrier_label):
        """Navigate, search, and export for one carrier. Returns (file, no_data)."""
        crawl.navigate_to_tender_request()
        crawl.set_search_conditions(run['logistics_groups'], carrier_id=carrier_id)
        if crawl.is_no_data():
            return None, True
        for attempt in range(5):
            log.info(f"Export attempt {attempt + 1}/5 ({carrier_label})")
            exported = crawl.export_results(download_dir, run['region'], carrier_label=carrier_label)
            if exported:
                return exported, False
            log.warning("Retrying")
        return None, False

    crawl = None
    a_file = b_file = None
    a_no_data = b_no_data = False
    a_error = b_error = False
    try:
        for login_attempt in range(3):
            try:
                crawl = TenderCrawl(username, password, download_dir)
                break
            except Exception:
                if login_attempt == 2:
                    raise
                log.warning(f"Login attempt {login_attempt + 1} failed, retrying...")

        try:
            a_file, a_no_data = run_export(crawl, CARRIER_IDS, 'ProviderA')
        except Exception:
            log.exception("Provider A export error")
            a_error = True

        try:
            b_file, b_no_data = run_export(crawl, CARRIER_IDS_PROVIDER_B, 'ProviderB')
        except Exception:
            log.exception("Provider B export error")
            b_error = True
    except Exception:
        log.exception("Login error")
        a_error = True
        b_error = True
    finally:
        if crawl:
            crawl.quit()

    date_today = today_str('a')
    notifier = load_sender()

    if notifier is None:
        _exit("Email sender unavailable, skipping notification")

    if a_file:
        body = build_tender_email_body(a_file, date_today)
        notifier.send(
            subject=cfg.EMAIL_SUBJECT.format(run['region'], date_today),
            body=body,
            to=cfg.EMAIL_TO,
            cc=run['email_cc'],
            bcc=cfg.EMAIL_BCC,
            attachment=a_file,
        )
    elif a_no_data:
        notifier.send(
            subject=cfg.EMAIL_SUBJECT_NO_DATA.format(run['region'], date_today),
            body='<BODY style="font-size:11pt;font-family:Calibri">No tender data was available for export. The system returned no results.</BODY>',
            to=cfg.EMAIL_TO_FAILURE,
            cc=cfg.EMAIL_CC_FAILURE,
        )
        log.warning("No Provider A data available, no data alert sent")
    elif a_error:
        notify_failure(notifier, cfg.EMAIL_SUBJECT_FAILURE.format(run['region'], date_today), cfg.EMAIL_TO_FAILURE, cfg.EMAIL_CC_FAILURE, _log_file)

    if b_file:
        body = build_tender_email_body(b_file, date_today)
        notifier.send(
            subject=cfg.EMAIL_SUBJECT_PROVIDER_B.format(run['region'], date_today),
            body=body,
            to=cfg.EMAIL_TO_PROVIDER_B,
            cc=run['email_cc'],
            bcc=cfg.EMAIL_BCC,
            attachment=b_file,
        )
    elif b_no_data:
        notifier.send(
            subject=cfg.EMAIL_SUBJECT_NO_DATA_PROVIDER_B.format(run['region'], date_today),
            body='<BODY style="font-size:11pt;font-family:Calibri">No tender data was available for export. The system returned no results.</BODY>',
            to=cfg.EMAIL_TO_FAILURE,
            cc=cfg.EMAIL_CC_FAILURE,
        )
        log.warning("No Provider B data available, no data alert sent")
    elif b_error:
        notify_failure(notifier, cfg.EMAIL_SUBJECT_FAILURE_PROVIDER_B.format(run['region'], date_today), cfg.EMAIL_TO_FAILURE, cfg.EMAIL_CC_FAILURE, _log_file)

    log.info("Done")
