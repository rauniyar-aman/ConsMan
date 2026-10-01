# Frontend deployment

Cloudflare Workers Builds connects directly to `rauniyar-aman/ConsMan`, branch `main`.

- Root directory: `frontend`
- Build command: `npm run build:cloudflare`
- Deploy command: `npm run deploy:cloudflare`
- Build variable: `API_ORIGIN=https://consman-rauniyaraman-api.onrender.com`
- Production custom domain: `consman.rauniyaraman.com.np`

New pushes after the GitHub connection is saved should appear in Cloudflare's build history. Check the build's commit and logs before treating it as deployed. A deploy hook is optional and is not required for GitHub push events. Keep preview builds disabled until a separate preview configuration is prepared.
