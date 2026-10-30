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
