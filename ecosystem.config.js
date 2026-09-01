// PM2 app definition. Add it alongside whatever else PM2 already runs:
//   pm2 start ecosystem.config.js && pm2 save
const path = require("node:path");

module.exports = {
  apps: [
    {
      name: "social-download-tg",
      // Resolved from this file's location, so the repo works for any user
      // at any path with no edits.
      cwd: __dirname,
      script: path.join(__dirname, ".venv/bin/python"),
      args: "bot.py",
      // Run the venv's python directly rather than through node.
      interpreter: "none",
      autorestart: true,
      restart_delay: 10000,
      max_memory_restart: "500M",
      env: {
        PYTHONUNBUFFERED: "1",
      },
    },
  ],
};
