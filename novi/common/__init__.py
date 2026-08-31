"""novi.common — neutral shared types with no ownership allegiance.

StableState lives here so both jobs (durable attempt state) and runtime
(execution mechanics) can import it without violating Task/Job/Runtime
boundary guards.
"""
