import {defineConfig} from '@playwright/test';
import base,{browserServer} from './playwright.config.js';
export default defineConfig({...base,testDir:'./tests/localization-browser',outputDir:'artifacts/localization-test-results',
  use:{...base.use,baseURL:'http://127.0.0.1:8002'},
  webServer:{...browserServer,command:'DRG_BROWSER_PORT=8002 '+browserServer.command,url:'http://127.0.0.1:8002/api/health'},
});
