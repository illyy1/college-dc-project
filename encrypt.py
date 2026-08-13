from cryptography.fernet import Fernet

# 1. Put your actual GitHub token here
original_token = "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"

# 2. Generate a secret key
key = Fernet.generate_key()
cipher_suite = Fernet(key)

# 3. Encrypt the token
encrypted_token = cipher_suite.encrypt(original_token.encode())

print("=== COPY THIS INTO YOUR PYTHON SCRIPT (main.py) ===")
print(f"ENCRYPTION_KEY = {key}")
print("\n=== COPY THIS INTO YOUR .env FILE ===")
print(f"GITHUB_TOKEN={encrypted_token.decode()}")