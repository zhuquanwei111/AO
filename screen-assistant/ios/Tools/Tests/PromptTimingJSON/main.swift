import Foundation

func valid(_ text: String) -> Bool { PromptTimingJSON.hasValidIntegers(Data(text.utf8)) }
assert(valid("{\"server_time_ms\":1,\"server_processing_ms\":0,\"prompts\":[{\"expires_at_ms\":9}]}"))
assert(!valid("{\"server_time_ms\":1.0,\"server_processing_ms\":0,\"prompts\":[]}"))
assert(!valid("{\"server_time_ms\":true,\"server_processing_ms\":0,\"prompts\":[]}"))
assert(!valid("{\"server_time_ms\":1,\"server_processing_ms\":0.0,\"prompts\":[]}"))
assert(!valid("{\"server_time_ms\":1,\"server_processing_ms\":0,\"prompts\":[{\"expires_at_ms\":9.0}]}"))
assert(!valid("{\"server_time_ms\":1,\"server_processing_ms\":0,\"prompts\":[{}]}"))
assert(!valid("{\"prompts\":[]}"))
print("OK: prompt timing JSON accepts integers only")
