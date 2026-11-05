// Только собственный CDO; значения не совпадают с индексами перечислителей.
enum EG1RMCPEnumProbeMode
{
    First = 0,
    Second = 1,
    Third = 7
};

UCLASS()
class UG1RMCPEnumProbe : UObject
{
    UPROPERTY()
    EG1RMCPEnumProbeMode StoredMode = EG1RMCPEnumProbeMode::First;

    UPROPERTY()
    int CallsCount = 0;

    UFUNCTION(BlueprintCallable)
    int ReadMarker()
    {
        return 81;
    }

    // Fresh local enum array; numeric values are First=0, Third=7; duplicates retained.
    UFUNCTION(BlueprintCallable)
    TArray<EG1RMCPEnumProbeMode> ReturnEnumArray(bool WithValues)
    {
        TArray<EG1RMCPEnumProbeMode> Result;
        if (WithValues)
        {
            Result.Add(EG1RMCPEnumProbeMode::First);
            Result.Add(EG1RMCPEnumProbeMode::Third);
            Result.Add(EG1RMCPEnumProbeMode::First);
        }
        return Result;
    }

    UFUNCTION(BlueprintCallable)
    EG1RMCPEnumProbeMode ReadMode()
    {
        return StoredMode;
    }

    UFUNCTION(BlueprintCallable)
    EG1RMCPEnumProbeMode EchoMode(EG1RMCPEnumProbeMode Input)
    {
        CallsCount += 1;
        return Input;
    }

    UFUNCTION(BlueprintCallable)
    void StoreMode(EG1RMCPEnumProbeMode Input)
    {
        CallsCount += 1;
        StoredMode = Input;
    }

    UFUNCTION(BlueprintCallable)
    void ResetProbe()
    {
        StoredMode = EG1RMCPEnumProbeMode::First;
        CallsCount = 0;
    }
}
