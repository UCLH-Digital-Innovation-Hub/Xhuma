# Benchmarking Suite

Xhuma includes a serverless load-testing suite powered by [Locust](https://locust.io/) and Azure Container Instances (ACI). The benchmarking suite allows you to safely test the API's performance and mTLS handling directly within the Azure VNet without needing to open up the internal App Service to the public internet.

## How It Works
The load tests are fully automated via GitHub Actions (`.github/workflows/load-test.yml`). 

When you trigger the workflow:
1. It authenticates with Azure.
2. It dynamically provisions an ephemeral Azure Container Instance (ACI) running Python inside the target environment's VNet (`rg-xhuma-int` or `rg-xhuma-uclh-prd`).
3. It securely passes the `tests/load_tests/locustfile.py` script to the container.
4. The container runs Locust in headless mode against the internal `.azurewebsites.net` FQDN of the App Service.
5. It retrieves the required mTLS certificates directly from Azure Key Vault using Managed Identity.
6. Once the test completes, the container base64-encodes the HTML results report and streams it to the GitHub Actions runner.
7. The container is immediately torn down to save costs.
8. The HTML report is available as an artifact download on the GitHub Actions run page.

## Running a Load Test

You can trigger a load test manually from the GitHub Actions tab. 

1. Go to **Actions** > **Serverless Benchmarking**.
2. Click **Run workflow**.
3. Configure your parameters:
   - **Users**: Number of concurrent users (Default: 5). *Note: Keep this low for the B1 App Service plan to avoid overwhelming it.*
   - **Spawn Rate**: Number of users to spawn per second (Default: 2).
   - **Duration**: How long to run the test (e.g., `3m`, `5m`) (Default: 3m).
   - **Environment**: Which environment to test (`int` or `main`).

## Viewing Results
Once the workflow completes:
1. Open the specific workflow run in GitHub Actions.
2. Scroll down to the **Artifacts** section.
3. Download the `benchmark-report-<env>.zip` file.
4. Unzip the file and open the `benchmark_report.html` file in your browser to view response times, failure rates, and throughput metrics.

## Modifying the Load Test
If you want to change the API endpoints being tested or add new user flows, modify the `tests/load_tests/locustfile.py` script. The CI pipeline will automatically encode and deploy the updated script during the next run.
