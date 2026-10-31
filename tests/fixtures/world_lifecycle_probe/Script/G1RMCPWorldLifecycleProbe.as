// Один callback производственного AS-контракта; изменяется только собственный счётчик.
UCLASS()
class UG1RMCPWorldPostInitializeProbe : UScriptWorldSubsystem
{
    UPROPERTY()
    int PostInitializeCount = 0;

    UFUNCTION(BlueprintOverride)
    void PostInitialize()
    {
        PostInitializeCount += 1;
    }

    UFUNCTION(BlueprintCallable)
    int ReadPostInitializeCount()
    {
        return PostInitializeCount;
    }

    UFUNCTION(BlueprintCallable)
    int ReadMarker()
    {
        return 111;
    }
}
