
# Cryptographically Secure Agents Example

This example demonstrates how to use cryptographic signing and verification to ensure secure communication between agents. Each message sent by an agent is signed using a private key, and the signature is verified by the receiving agent using the corresponding public key.

## Key Features
- **Message Signing**: Messages are signed using Ed25519 private keys.
- **Signature Verification**: The receiving agent verifies the signature using the sender's public key.
- **Payload Integrity**: Ensures that the message content has not been tampered with during transmission.

## Components
1. **`chat_agent.py`**: A client agent that sends and receives cryptographically signed messages.
2. **`myserver.py`**: A server that facilitates communication between agents.
3. **`server_config.json`**: Configuration file for the server, specifying parameters like host, port, and rate limits.

## How It Works
1. **Key Generation**: The client generates a private key (`SECRET_KEY`) and derives the corresponding public key.
2. **Message Signing**: Before sending a message, the client signs it using the private key and includes the signature and public key in the payload.
3. **Message Verification**: Upon receiving a message, the client verifies the signature using the public key. If the verification fails, the message is discarded.

## Setup Instructions
1. **Start the Server**:
   ```bash
   python myserver.py --config server_config.json
   ```
2. **Run the Client**:
   ```bash
   python chat_agent.py
   ```
3. **Send Messages**:
   - Type a message in the client terminal to send it.
   - Received messages will be displayed in the terminal if the signature is valid.

## Example Interaction
1. Start the server:
   ```bash
   python myserver.py --config server_config.json
   ```
2. Start the client:
   ```bash
   python chat_agent.py
   ```
3. In the client terminal, type:
   ```
   s> Hello, world!
   ```
4. The message will be sent to the server, signed, and verified by other agents.

## Security Notes
- Ensure the private key (`SECRET_KEY`) is kept secure and not shared.
- Public keys are included in the message payload for verification purposes.

This example demonstrates the basics of cryptographically secure communication and can be extended for more complex use cases.
