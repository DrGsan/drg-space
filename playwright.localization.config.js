import {defineConfig} from '@playwright/test';
import base from './playwright.config.js';
export default defineConfig({...base,testDir:'./tests/localization-browser',outputDir:'artifacts/localization-test-results',
  use:{...base.use,baseURL:'http://127.0.0.1:8002'},
  webServer:{...base.webServer,command:'DRG_BROWSER_PORT=8002 '+base.webServer.command,url:'http://127.0.0.1:8002/api/health'},
});
