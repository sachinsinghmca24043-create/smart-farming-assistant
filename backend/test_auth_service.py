from services.auth_service import (
    register_farmer,
    login_farmer,
    get_farmer_from_token,
    logout_farmer,
)


def test_farmer_authentication():
    email = "test_farmer@example.com"
    password = "Test@12345"

    # Register
    try:
        farmer = register_farmer(
            name="Test Farmer",
            email=email,
            password=password,
        )
        print("Registration:", farmer)
    except ValueError as exc:
        if "already exists" in str(exc):
            print("Farmer already registered. Testing login.")
        else:
            raise

    # Login
    result = login_farmer(email, password)
    token = result["access_token"]

    print("Login:", result["user"])

    # Verify token
    authenticated_user = get_farmer_from_token(token)
    print("Token valid:", authenticated_user is not None)

    # Logout
    logged_out = logout_farmer(token)
    print("Logout:", logged_out)

    # Verify revoked token
    print("Token invalid after logout:", get_farmer_from_token(token) is None)


if __name__ == "__main__":
    test_farmer_authentication()