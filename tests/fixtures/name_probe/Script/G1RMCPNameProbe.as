// Только собственный объект, чтение констант и неизменных свойств.
UCLASS()
class UG1RMCPNameProbe : UObject
{
    UPROPERTY()
    FName StoredEntry = n"Entry";

    UPROPERTY()
    FName StoredNone = n"";

    UFUNCTION(BlueprintCallable)
    int ReadMarker()
    {
        return 71;
    }

    UFUNCTION(BlueprintCallable)
    FName ReadEntry()
    {
        return n"Entry";
    }

    UFUNCTION(BlueprintCallable)
    FName ReadNone()
    {
        return n"";
    }

    UFUNCTION(BlueprintCallable)
    FString ReadText()
    {
        return "G1R MCP dev7";
    }

    UFUNCTION(BlueprintCallable)
    FString ReadEmptyText()
    {
        return "";
    }
}
