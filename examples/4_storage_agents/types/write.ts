/**
 * Represents a write operation with context and operations for a Software Transactional Memory (STM) system.
 * 
 * This design implements optimistic concurrency control in distributed systems by:
 * 1. Capturing the expected state of relevant records (context)
 * 2. Defining the mutations to perform (operations)
 * 3. Allowing the system to validate consistency before committing
 * 
 * STM enables atomic, isolated transactions across distributed nodes without requiring locks.
 * Instead, it detects conflicts at commit-time by comparing expected versions against actual versions.
 */
type Write = {
    idempotency: number; // Unique identifier, used for idempotency and coordination.

    /**
     * Context represents the "snapshot" of the world when this transaction was created.
     * It contains all records the transaction depends on with their expected versions.
     * 
     * At commit time, the system validates that all context records still match these versions.
     * If any version has changed, it means another transaction modified data this transaction
     * depends on, and the transaction will be aborted to prevent inconsistency.
     * 
     * This implements the "optimistic" part of optimistic concurrency control - we proceed
     * assuming no conflicts, but verify before committing.
     */
    context: Array<{
      type: number;   // Type identifier for the record (64-bit integer)
      guid: number;   // Globally unique identifier for the record (128-bit integer)
      version: number; // Expected version - transaction aborts if actual version differs
    }>;
  
    /**
     * Operations define the actual changes to be made atomically in this transaction.
     * The system ensures that either ALL operations succeed or NONE do.
     * 
     * Each operation identifies a record and provides its new value. The version field
     * serves dual purposes:
     * - For inserts (version=0): Create a new record with this value
     * - For updates (version>0): Update existing record, but only if current version matches
     * 
     * After successful commit, all updated records have their version incremented,
     * which automatically invalidates any concurrent transactions touching the same records.
     */
    operations: Array<{
      type: number;    // Type identifier for record validation and routing
      guid: number;    // Globally unique identifier for the target record
      version: number; // 0 for inserts (new records), >0 for updates (must match current version)
      
      /**
       * The new value to write.
       * For inserts: The initial record state
       * For updates: The new record state (completely replaces previous value)
       */
      value: any;      // Any JSON-serializable value (objects, arrays, primitives, null)
    }>;
  };