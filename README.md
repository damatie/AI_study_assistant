# AI Study Assistant

## Project Description

The AI Study Assistant is a Python-based web application built with FastAPI, designed to provide users with tools and resources to enhance their learning experience. It offers features such as:

- User authentication and management
- Subscription handling
- Material processing
- Usage tracking
- Email handling

## Setup Instructions

1.  **Clone the repository:**

    ```bash
    git clone <repository_url>
    cd AI_study_assistant
    ```

2.  **Create a virtual environment:**

    ```bash
    python -m venv venv
    ```

3.  **Activate the virtual environment:**

    - **On Windows:**

      ```bash
      venv\\Scripts\\activate
      ```

    - **On macOS and Linux:**

      ```bash
      source venv/bin/activate
      ```

4.  **Install the dependencies:**

    ```bash
    pip install -r requirements.txt
    ```

5.  **Configure the application:**

    - Create a `.env` file based on the provided `.env.example` file.
    - Update the `.env` file with your specific configuration values, such as database credentials, email settings, and API keys.
    - **Note:** Ensure that the `.env` file is not committed to the repository for security reasons.

6.  **Run the database migrations:**

    ```bash
    # Assuming you have Alembic set up for migrations
    alembic upgrade head
    ```

7.  **Start the application:**

    ```bash
    python app/main.py
    ```

## Usage

1.  **Access the API:**

    - The API endpoints are available at `/api/v1`.
    - Refer to the API documentation (Swagger/OpenAPI) for detailed information on available endpoints, request parameters, and response formats.

2.  **User Authentication:**

    - Use the `/auth` endpoints to register, login, and manage user accounts.

3.  **Subscription Management:**

    - Use the `/subscription` endpoints to manage user subscriptions and plans.

4.  **Material Processing:**

    - Utilize the material processing services to handle and process study materials.

5.  **Usage Tracking:**

    - The application tracks user activity and usage patterns to provide insights and analytics.

## Contribution

Contributions are welcome! If you'd like to contribute to the project, please follow these steps:

1.  Fork the repository.
2.  Create a new branch for your feature or bug fix.
3.  Implement your changes and ensure they are well-tested.
4.  Submit a pull request with a clear description of your changes.

## Contributing Guidelines

Read the [Contributing Guidelines](CONTRIBUTING.md) for detailed steps on how to contribute to this project.

## License

This project is licensed under the MIT License - see the LICENSE file for details.
